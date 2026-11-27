"""Return one source death's exact physical arrivals at the next end step."""

from collections.abc import Mapping

from ..continuous_effect_state import ResolutionEffectSource
from ..delayed_object_model import (
    OBJECT_TIMINGS, RESOLVE_OBJECT_ACTION, SCHEDULE_OBJECT_ACTION, SOURCE_DEATH_HAND_CAPABILITY,
    SOURCE_DEATH_HAND_DEPENDENCIES, SOURCE_DEATH_HAND_EVENT, source_death_hand_return_effect,
    uses_source_death_hand_return, validate_source_death_hand_return,
)
from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap
from .death_battlefield_return import DEATH_BATTLEFIELD_ARRIVALS, decode_death_battlefield_arrivals
from .effect_shape_walkers import nested_effects


def source_death_hand_runtime_dependencies(program):
    effects = tuple(nested_effects((program.effects, program.handlers, program.target_schema)))
    if not (any(uses_source_death_hand_return(effect) for effect in effects)
            or SOURCE_DEATH_HAND_CAPABILITY in program.capability_dependencies):
        return ()
    if ((program.event, program.active_zone) != (SOURCE_DEATH_HAND_EVENT, "battlefield")
            or program.event_condition is not None or program.target_schema is not None
            or program.cost_schema is not None or program.handlers or len(program.effects) != 1):
        raise ValueError("Delayed owner-hand return requires one original untargeted self-death trigger")
    validate_source_death_hand_return(program.effects[0], compiled=True)
    return SOURCE_DEATH_HAND_DEPENDENCIES


def bind_source_death_hand_return(effect, item):
    facts = item.context
    identity = facts.get("source_logical_object_id")
    if (item.kind != "triggered_ability" or facts.get("event") != "permanent.graveyard"
            or facts.get("from") != "battlefield" or facts.get("to") != "graveyard"
            or type(identity) is not str or not identity or not item.source_object_id
            or facts.get("card_object_identity") != identity):
        raise ValueError("Delayed owner-hand return lost its original source death")
    arrivals = decode_death_battlefield_arrivals(facts.get(DEATH_BATTLEFIELD_ARRIVALS))
    if facts.get("composition") is None and arrivals:
        row, = arrivals
        if (row.object_id != item.source_object_id or row.reference != facts.get("card")
                or row.owner != facts.get("owner") or row.zone_change_counter != facts.get("card_zone_change_counter")):
            raise ValueError("Delayed owner-hand return has a different physical arrival")
    return {**effect, "object": [row.to_dict() for row in arrivals]}


def _resolving_item(host, effect, actor):
    source = ResolutionEffectSource.from_effect(effect)
    item = next((row for row in host.state.stack if row.ref == source.stack_ref), None)
    if (item is None or item.kind != "triggered_ability" or item.controller != actor
            or item.context.get("currently_resolving") is not True):
        raise ValueError("Delayed owner-hand return requires its original resolving trigger")
    return item


def _schedule_return(host, item, actor, instruction, arrivals):
    from ..trigger_processing import schedule_delayed_trigger

    compiled = source_death_hand_return_effect()
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not source_death_hand_runtime_dependencies(program)
            or FrozenMap(instruction) != FrozenMap(bind_source_death_hand_return(compiled, item))):
        raise ValueError("Delayed owner-hand return does not match its original ability")
    future = {**instruction, "op": RESOLVE_OBJECT_ACTION}
    future.pop("timing")
    return schedule_delayed_trigger(
        host, controller=actor, label="Return the original graveyard card to its owner's hand",
        event_kind="step.begin", condition=OBJECT_TIMINGS["next_end_step"],
        stack_template={"context": {"dynamic_effects": [future]}}, once=True,
        source_object_id=item.source_object_id, source_logical_object_id=item.context["source_logical_object_id"],
        referred_object_ids=tuple(row.object_id for row in arrivals),
    ).ref


def _resolve_return(host, item, actor, instruction, arrivals, reason):
    from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent

    original = item.context.get("dynamic_effects", ())
    saved = {key: value for key, value in instruction.items() if key != "_replacement_selections"}
    if (item.context.get("event") != "step.begin" or not item.context.get("delayed_trigger_ref")
            or not isinstance(original, (list, tuple)) or len(original) != 1
            or not isinstance(original[0], Mapping) or FrozenMap(original[0]) != FrozenMap(saved)):
        raise ValueError("Delayed owner-hand return lost the sealed step occurrence")
    references = tuple(row.reference for row in arrivals
                       if row.owner in host.active_seats and row.matches(host.state.cards.get(row.object_id)))
    if not references:
        return ()
    return host.move_objects_simultaneously_intent(MoveObjectsSimultaneouslyIntent(
        actor=actor, object_refs=references, expected_zones=("graveyard",), destination="hand",
        reason=reason, replacement_selections=tuple(instruction.get("_replacement_selections", ())),
    ))


def apply_source_death_hand_return(host, effect, instruction, *, actor, reason):
    try:
        validate_source_death_hand_return(instruction)
        arrivals = decode_death_battlefield_arrivals(instruction["object"])
        item = _resolving_item(host, effect, actor)
        if instruction["op"] == SCHEDULE_OBJECT_ACTION:
            return _schedule_return(host, item, actor, instruction, arrivals)
        return _resolve_return(host, item, actor, instruction, arrivals, reason)
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
