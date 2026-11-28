"""Schedule a watched battlefield death and bind its departure counter snapshot."""

from dataclasses import replace

from ..continuous_effect_model import ContinuousObjectIdentity
from ..counter_snapshot import permanent_counter_snapshot
from ..errors import GameRuleError
from ..target_source import BOUND_TARGETING_SOURCE_VERSION, TARGETING_SOURCE_CONTEXT, capture_targeting_source
from ..trigger_processing import TriggerProcessingOwner, schedule_delayed_trigger
from .controller_damage_redirection import creature_redirection_target_schema
from .departure_counter_transfer import DEPARTURE_COUNTER_SNAPSHOT_FIELD, departure_counter_effect


EVENT = "permanent.graveyard.watched-counter-draw.v1"
IDENTITY = "watched_battlefield_incarnation"
_LABEL = "Put the departed permanent's counters on up to one creature and draw"


def schedule_watched_death(host, source, destination, *, actor):
    target = ContinuousObjectIdentity(destination.object_id, destination.logical_object_id)
    card = host.state.cards.get(source.object_id)
    context = {}
    if card is not None and card.logical_object_id == source.logical_object_id:
        binding = capture_targeting_source(host, card, schema_version=BOUND_TARGETING_SOURCE_VERSION)
        context[TARGETING_SOURCE_CONTEXT] = binding.to_dict()
    return schedule_delayed_trigger(
        host, controller=actor, label=_LABEL, event_kind=EVENT,
        condition={IDENTITY: target.to_dict()}, stack_template={"context": context},
        source_object_id=source.object_id, source_logical_object_id=source.logical_object_id,
        referred_object_ids=(target.object_id,), once=True, expires_turn_sequence=host.state.turn_sequence,
    )


def collect_watched_death_counter_draw(host, occurrence, pending) -> None:
    """Create the ordinary delayed stack item from a committed departure."""
    if (occurrence.origin, occurrence.destination) != ("battlefield", "graveyard"):
        return
    identity = ContinuousObjectIdentity(occurrence.object_id, occurrence.previous_logical_object_id)
    owner = TriggerProcessingOwner(host)
    matches = owner.matching_delayed_triggers(EVENT, {IDENTITY: identity.to_dict()})
    for trigger in matches:
        if trigger.controller not in host.active_seats:
            continue
        pending.append(_watched_death_item(owner, trigger, occurrence, identity))


def _watched_death_item(owner, trigger, occurrence, identity):
    template = trigger.stack_template
    retained = template.get("context")
    if (trigger.condition != {IDENTITY: identity.to_dict()} or trigger.once is not True
            or trigger.expires_turn_sequence != trigger.created_turn_sequence
            or trigger.referred_object_ids != [identity.object_id]
            or set(template) != {"context"} or not isinstance(retained, dict)
            or set(retained) - {TARGETING_SOURCE_CONTEXT}):
        raise GameRuleError("Watched death requires its complete original delayed trigger")
    try:
        counters = permanent_counter_snapshot(occurrence.previous_counters)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    effects = [departure_counter_effect()] if counters else []
    effects.append({"op": "draw", "count": 1, "private": True})
    context = {
        **retained,
        "dynamic_effects": effects,
        "target_schema_override": creature_redirection_target_schema(optional=True),
        "trigger_target_selection_pending": True,
        DEPARTURE_COUNTER_SNAPSHOT_FIELD: dict(counters),
    }
    return owner.materialize_delayed_trigger(replace(trigger, stack_template={"context": context}))
