"""Read the original battlefield source of a conditional next-end-step return."""

from collections.abc import Mapping

from ..characteristic_evaluation import type_parts
from ..continuous_effect_model import ContinuousObjectIdentity
from ..continuous_effect_state import ResolutionEffectSource
from ..delayed_object_model import (
    RESOLVE_OBJECT_ACTION, SCHEDULE_OBJECT_ACTION, SMALL_CREATURE_DEATH_ARRIVALS,
    SOURCE_BOUND_CREATURE_RETURN_CAPABILITY, SOURCE_BOUND_CREATURE_RETURN_DEPENDENCIES,
    SOURCE_BOUND_RETURN_ACCEPTED, SOURCE_BOUND_RETURN_OFFER, decode_source_bound_return_arrivals,
    is_source_bound_creature_return, small_creature_owner_graveyard_condition,
    source_bound_creature_return_effect, validate_source_bound_creature_return,
)
from ..errors import GameRuleError
from ..external_entry_state_model import PERMANENT_CARD_TYPES
from ..replacement.immutable import FrozenMap
from .committed_quantity_followups import CommittedQuantityFollowup


SOURCE_BOUND_NEXT_END_STEP = "source_bound_next_end_step_version"


def source_bound_end_step_condition() -> dict[str, str | int]:
    return {"phase": "ending", "step": "end_step", SOURCE_BOUND_NEXT_END_STEP: 1}


def delayed_return_source_present(state, source: ContinuousObjectIdentity) -> bool:
    card = state.cards.get(source.object_id)
    return (card is not None and card.logical_object_id == source.logical_object_id
            and card.zone == "battlefield" and not card.phased_out)


def source_bound_end_step_result(state, trigger, context) -> bool | None:
    """Read the intervening condition at the first due step."""
    if (trigger.event_kind != "step.begin" or trigger.once is not True
            or type(trigger.condition[SOURCE_BOUND_NEXT_END_STEP]) is not int
            or trigger.condition != source_bound_end_step_condition()):
        raise ValueError("A source-bound return requires its exact one-shot end-step condition")
    source = ContinuousObjectIdentity(trigger.source_object_id, trigger.source_logical_object_id)
    if (context.get("phase"), context.get("step")) != ("ending", "end_step"):
        return None
    return delayed_return_source_present(state, source)



def small_creature_death_context(host, context, occurrence, parts):
    """Seal only the original physical cards entering the graveyard."""
    from .event_subscriptions import event_has_capability_subscriber

    if not event_has_capability_subscriber(host, "permanent.graveyard", SOURCE_BOUND_CREATURE_RETURN_CAPABILITY):
        return context
    group = occurrence.composition
    if group is None:
        arrivals = (occurrence,)
    else:
        if (not group.permanent_departure or len(parts) != group.member_count or any(
            part.composition is None or not part.composition.physical_arrival or part.composition.parent != group.parent
            for part in parts
        )):
            raise ValueError("Source-bound return requires its complete physical departure")
        arrivals = parts
    return {**context, SMALL_CREATURE_DEATH_ARRIVALS: [
        ContinuousObjectIdentity(part.object_id, part.current_logical_object_id).to_dict()
        for part in arrivals if part.destination == "graveyard" and part.card_object and not part.token
    ]}


def bind_source_bound_return(effect, item):
    if item.context.get("event") != "permanent.graveyard" or item.context.get("to") != "graveyard":
        raise ValueError("Source-bound return lost its original graveyard occurrence")
    source = ContinuousObjectIdentity(item.source_object_id, item.context.get("source_logical_object_id"))
    arrivals = decode_source_bound_return_arrivals(item.context.get(SMALL_CREATURE_DEATH_ARRIVALS))
    return {**effect, "source": source.to_dict(), "object": [part.to_dict() for part in arrivals]}


def source_bound_return_runtime_dependencies(program):
    from .source_death_delayed_hand_return import source_death_hand_runtime_dependencies
    from .effect_shape_walkers import nested_effects
    if required := source_death_hand_runtime_dependencies(program):
        return required

    if not any(is_source_bound_creature_return(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if (program.event != "permanent.graveyard" or program.active_zone != "battlefield"
            or program.target_schema is not None or program.handlers
            or tuple(program.effects) != (source_bound_creature_return_effect(),)
            or FrozenMap(program.event_condition or {}) != FrozenMap(small_creature_owner_graveyard_condition())):
        raise ValueError("Source-bound return requires its exact low-power owned graveyard occurrence")
    return SOURCE_BOUND_CREATURE_RETURN_DEPENDENCIES


def _source_bound_resolving_item(host, runtime_source, source, actor):
    resolver = ResolutionEffectSource.from_effect({"_runtime_source": runtime_source})
    item = next((item for item in host.state.stack if item.ref == resolver.stack_ref), None)
    if (item is None or item.controller != actor or item.source_object_id != source.object_id
            or item.context.get("source_logical_object_id") != source.logical_object_id
            or item.context.get("currently_resolving") is not True):
        raise ValueError("Source-bound return lost the original resolving source")
    return item


def _schedule_source_bound_return(host, instruction, item, source, actor, arrivals):
    from ..trigger_processing import schedule_delayed_trigger

    try:
        sealed = decode_source_bound_return_arrivals(item.context.get(SMALL_CREATURE_DEATH_ARRIVALS))
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    if item.context.get("event") != "permanent.graveyard" or sealed != arrivals:
        raise GameRuleError("Source-bound return lost its original graveyard arrivals")
    future = {key: value for key, value in instruction.items() if key != "timing"}
    future["op"] = RESOLVE_OBJECT_ACTION
    label = "Return the card from the original graveyard occurrence"
    return schedule_delayed_trigger(
        host, controller=actor, label=label, event_kind="step.begin", condition=source_bound_end_step_condition(),
        stack_template={"context": {"dynamic_effects": [future]}}, once=True,
        source_object_id=source.object_id, source_logical_object_id=source.logical_object_id,
        referred_object_ids=[arrival.object_id for arrival in arrivals],
    ).ref


def _eligible_return_cards(host, arrivals):
    cards = []
    for arrival in arrivals:
        card = host.state.cards.get(arrival.object_id)
        if (card is not None and card.logical_object_id == arrival.logical_object_id
                and card.zone == "graveyard" and card.is_card_object and not card.is_token
                and type_parts(str(host._effective_card_data(card).get("type_line") or ""))[0] & PERMANENT_CARD_TYPES):
            cards.append(card)
    return tuple(cards)


def _resolve_source_bound_return(host, instruction, item, source, actor, reason, arrivals):
    original = item.context.get("dynamic_effects", ())
    offer = instruction["action"] == SOURCE_BOUND_RETURN_OFFER
    restored = {key: value for key, value in instruction.items()
                if key not in {"_aura_target_refs", "_replacement_selections"}}
    restored["action"] = SOURCE_BOUND_RETURN_OFFER
    if (item.context.get("event") != "step.begin" or not item.context.get("delayed_trigger_ref")
            or not isinstance(original, (list, tuple)) or len(original) != 1 or not isinstance(original[0], Mapping)
            or FrozenMap(original[0]) != FrozenMap(restored)):
        raise GameRuleError("Source-bound return lost the original delayed instruction")
    cards = _eligible_return_cards(host, arrivals) if delayed_return_source_present(host.state, source) else ()
    if offer:
        if not cards:
            return CommittedQuantityFollowup(())
        return CommittedQuantityFollowup((FrozenMap({
            "op": "offer_optional_effect", "player": actor,
            "effects": [{**restored, "action": SOURCE_BOUND_RETURN_ACCEPTED}],
        }),))
    if not cards:
        return ()
    from ..zone_transitions import ZoneTransitionOwner

    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        tuple((card.object_id, "battlefield") for card in cards), reason=reason,
        destination_controllers={card.object_id: actor for card in cards},
        putting_players={card.object_id: actor for card in cards},
        replacement_selections=tuple(instruction.get("_replacement_selections", ())),
        aura_target_refs=instruction.get("_aura_target_refs"),
    )
    return tuple(card.ref for card in moved if card.zone == "battlefield")


def apply_source_bound_return(host, instruction, *, runtime_source, actor, reason):
    try:
        validate_source_bound_creature_return(instruction)
        source = ContinuousObjectIdentity.from_dict(instruction["source"])
        arrivals = decode_source_bound_return_arrivals(instruction["object"])
        item = _source_bound_resolving_item(host, runtime_source, source, actor)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    if instruction["op"] == SCHEDULE_OBJECT_ACTION:
        return _schedule_source_bound_return(host, instruction, item, source, actor, arrivals)
    return _resolve_source_bound_return(host, instruction, item, source, actor, reason, arrivals)
