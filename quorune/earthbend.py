from __future__ import annotations

"""Apply Earthbend through continuous effects, counter placement, and zone owners."""

from dataclasses import replace

from .characteristic_evaluation import type_parts
from .continuous_effect_model import ContinuousEffectDuration, ContinuousOperation, Layer
from .continuous_effect_state import (
    ResolutionContinuousComponent, ResolutionEffectSource, create_resolution_continuous_effect_components,
)
from .earthbend_model import (
    EARTHBEND_DEPARTURE, EARTHBEND_EVENT, EARTHBEND_FINISH_OPERATION,
    EARTHBEND_RETURN_OPERATION, EARTHBEND_RETURN_ZONES, EarthbendObject,
)
from .errors import GameRuleError
from .public_zone_moves import PublicBattlefieldController
from .replacement.immutable import FrozenMap
from .rules.committed_quantity_followups import CommittedQuantityFollowup
from .semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
from .trigger_processing import TriggerProcessingOwner


def animate_earthbend_land(host, intent) -> CommittedQuantityFollowup:
    land = host._resolve_object(
        intent.actor, intent.target, zones={"battlefield"}, controlled_only=True,
    )
    types, _subtypes, _supertypes = type_parts(host._effective_card_data(land)["type_line"])
    if land.phased_out or "land" not in types:
        raise GameRuleError("Earthbend requires a current land its controller controls")
    binding = EarthbendObject(
        land.object_id, land.logical_object_id, land.ref, land.zone, land.zone_change_counter,
    )
    create_resolution_continuous_effect_components(
        host, source=ResolutionEffectSource(**intent.source.to_dict()), targets=(land,),
        components=(
            ResolutionContinuousComponent(
                Layer.TYPE, "4", (ContinuousOperation("add_types", ["Creature"], field="card_types"),),
            ),
            ResolutionContinuousComponent(Layer.ABILITY, "6", (ContinuousOperation("add_ability", "Haste"),)),
            ResolutionContinuousComponent(
                Layer.POWER_TOUGHNESS, "7b", (ContinuousOperation("set_power_toughness", [0, 0]),),
            ),
        ),
        duration=ContinuousEffectDuration.ZONE_OBJECT,
    )
    effects = []
    if intent.amount:
        effects.append(FrozenMap({
            "op": "place_counters", "card": land.ref, "counter": "+1/+1",
            "amount": intent.amount, "source": intent.source.stack_ref,
        }))
    effects.append(FrozenMap({
        "op": EARTHBEND_FINISH_OPERATION, "land": binding.to_dict(), "stack_ref": intent.source.stack_ref,
    }))
    return CommittedQuantityFollowup(tuple(effects))


def finish_earthbend_land(host, intent) -> str:
    land = intent.land
    label = "Earthbend — return the land"
    trigger = TriggerProcessingOwner(host).schedule_delayed_trigger(
        controller=intent.actor, label=label, event_kind=EARTHBEND_DEPARTURE,
        condition={"land_identity": land.logical_object_id},
        stack_template={"label": label}, source_object_id=intent.source.object_id,
        source_logical_object_id=intent.source.logical_object_id, once=True,
        referred_object_ids=(land.object_id,),
    )
    host._dispatch_semantic_event(
        EARTHBEND_EVENT, {
            "player": intent.actor, "card": land.reference,
            "card_object_identity": land.logical_object_id, "earthbend_trigger_ref": trigger.ref,
        },
    )
    return trigger.ref


def collect_earthbend_departure(host, occurrence, composition_departures, pending) -> None:
    if (
        occurrence.origin != "battlefield" or occurrence.destination not in EARTHBEND_RETURN_ZONES
        or occurrence.composition is not None and not occurrence.composition.permanent_departure
        or not getattr(getattr(host, "state", None), "delayed_triggers", ())
    ):
        return
    owner = TriggerProcessingOwner(host)
    matches = owner.matching_delayed_triggers(
        EARTHBEND_DEPARTURE, {"land_identity": occurrence.previous_logical_object_id},
    )
    if not matches:
        return
    departures = composition_departures if occurrence.composition is not None else (occurrence,)
    if occurrence.composition is not None:
        parent = occurrence.composition
        if len(departures) != parent.member_count or any(
            fact.composition is None or not fact.composition.physical_arrival or fact.composition.parent != parent.parent
            for fact in departures
        ):
            raise GameRuleError("Earthbend requires the committed physical departure facts")
    objects = tuple(
        EarthbendObject(
            fact.object_id, fact.current_logical_object_id, fact.card_ref, fact.destination, fact.zone_change_counter,
        )
        for fact in departures
        if fact.destination in EARTHBEND_RETURN_ZONES and fact.card_object and not fact.token
    )
    effect = {"op": EARTHBEND_RETURN_OPERATION, "objects": [value.to_dict() for value in objects]}
    for trigger in matches:
        pending.append(owner.materialize_delayed_trigger(replace(
            trigger, stack_template={"label": trigger.label, "context": {"dynamic_effects": [effect]}},
            referred_object_ids=[value.object_id for value in objects],
        )))


def return_earthbent_objects(host, intent) -> tuple[str, ...]:
    references = []
    for binding in intent.objects:
        card = host.state.cards.get(binding.object_id)
        if (
            card is not None and card.ref == binding.reference and card.zone == binding.zone
            and card.logical_object_id == binding.logical_object_id
            and card.zone_change_counter == binding.zone_change_counter
            and card.is_card_object and not card.is_token
        ):
            references.append(card.ref)
    if not references:
        return ()
    return host.move_objects_simultaneously_intent(MoveObjectsSimultaneouslyIntent(
        actor=intent.actor, object_refs=tuple(references), expected_zones=("graveyard", "exile"),
        destination="battlefield", reason="Earthbend return", replacement_selections=intent.replacement_selections,
        battlefield_controller=PublicBattlefieldController.ACTOR, battlefield_tapped=True,
    ))
