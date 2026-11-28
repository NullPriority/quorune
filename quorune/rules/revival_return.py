from __future__ import annotations

"""Return the exact cards from a self-death with one fewer revival counter."""

from collections.abc import Mapping

from ..continuous_effect_state import ResolutionEffectSource
from ..counter_snapshot import permanent_counter_snapshot
from ..entry_counter_model import EffectEntryCounter
from ..errors import GameRuleError
from ..external_entry_state_model import PERMANENT_CARD_TYPES
from ..replacement.immutable import FrozenMap
from .death_battlefield_return import DEATH_BATTLEFIELD_ARRIVALS, decode_death_battlefield_arrivals

CAPABILITY = "zone.return.self_death_delayed_revival"
MECHANIC = "self-death-delayed-revival-return"
VERSION_FIELD = "self_death_revival_return_version"
COUNT_FIELD = "revival_death_count"
ARRIVAL_FIELD = "revival_death_arrivals"
CONDITION = {"field": "counter_names", "op": "contains_any", "value": ["revival"]}
END_STEP = {"phase": "ending", "step": "end_step"}
DEPENDENCIES = (
    CAPABILITY, "attachment.aura.typed_restriction", "counter.placement.quantity_replacement",
    "counter.producer.effect_entry", "trigger.delayed.fixed_object_action",
    "trigger.event.normalized_zone_change", "trigger.placement.apnap", "zone.change.destination_replacement",
)
_BODY = (
    "if it had a revival counter on it, return it to the battlefield with one fewer revival counter on it "
    "at the beginning of the next end step."
)


def _effect(phase, count, arrivals):
    return {"op": "reanimate", VERSION_FIELD: 1, "phase": phase, COUNT_FIELD: count, ARRIVAL_FIELD: arrivals}


def _schedule_effect():
    return _effect("schedule", "$context." + COUNT_FIELD, "$context." + DEATH_BATTLEFIELD_ARRIVALS)


def revival_death_result(body):
    if " ".join(body.casefold().split()) != _BODY:
        return None
    return "self-death-delayed-revival-v1", (_schedule_effect(),), None, (MECHANIC,), CONDITION


def is_revival_return(effect):
    return effect.get("op") == "reanimate" and VERSION_FIELD in effect


def revival_return_node_capabilities(effects, target_schema):
    if (target_schema is None and len(effects) == 1 and dict(effects[0]) == _schedule_effect()
            and type(effects[0][VERSION_FIELD]) is int):
        return DEPENDENCIES
    return ()


def revival_return_runtime_dependencies(program):
    from .effect_shape_walkers import contains_nested_key

    marked = CAPABILITY in program.capability_dependencies or MECHANIC in program.coverage or contains_nested_key(
        (program.effects, program.target_schema, program.handlers), VERSION_FIELD,
    )
    if not marked:
        return ()
    required = revival_return_node_capabilities(program.effects, program.target_schema)
    if (not required or (program.event, program.active_zone) != ("creature.dies.self", "battlefield")
            or program.cost_schema is not None or program.handlers
            or FrozenMap(program.event_condition or {}) != FrozenMap(CONDITION) or MECHANIC not in program.coverage):
        raise ValueError("Revival return requires its original self-death condition and next end step")
    return required


def capture_revival_death_count(context, occurrence):
    if occurrence.previous_counters is None:
        raise ValueError("Revival death requires the committed last-known counter snapshot")
    return {**context, COUNT_FIELD: permanent_counter_snapshot(occurrence.previous_counters).get("revival", 0)}


def validate_resolved_revival_return(effect, *, required_phase=None):
    phase = effect.get("phase")
    count = effect.get(COUNT_FIELD)
    required = set(_schedule_effect())
    allowed = {"_runtime_source", "reason"}
    if phase == "return":
        allowed.update(("_replacement_selections", "_aura_target_refs"))
    if (not required <= set(effect) <= required | allowed or not is_revival_return(effect)
            or type(effect[VERSION_FIELD]) is not int or effect[VERSION_FIELD] != 1
            or phase not in ("schedule", "return") or required_phase is not None and phase != required_phase
            or type(count) is not int or count < 1
            or not isinstance(effect.get("_replacement_selections", ()), (tuple, list))):
        raise ValueError("Revival return has an invalid resolving instruction")
    arrivals = decode_death_battlefield_arrivals(effect[ARRIVAL_FIELD])
    aura_refs = effect.get("_aura_target_refs", {})
    if (not isinstance(aura_refs, Mapping) or not set(aura_refs) <= {row.object_id for row in arrivals}
            or any(type(ref) is not str or not ref for ref in aura_refs.values())):
        raise ValueError("Revival return lost its physical Aura entry selections")
    return count, arrivals


def _schedule_return(host, item, count, arrivals):
    from ..trigger_processing import schedule_delayed_trigger

    context = item.context
    program = host.semantics.get(item.semantic_key)
    if (program is None or not revival_return_runtime_dependencies(program)
            or context.get("event") != "creature.dies" or context.get("from") != "battlefield"
            or context.get("to") != "graveyard" or context.get("card_object_identity") != context["source_logical_object_id"]
            or type(context.get(COUNT_FIELD)) is not int or context[COUNT_FIELD] != count
            or decode_death_battlefield_arrivals(context[DEATH_BATTLEFIELD_ARRIVALS]) != arrivals):
        raise ValueError("Revival return lost its exact source death and last-known count")
    if arrivals:
        return schedule_delayed_trigger(
            host, controller=item.controller, label="Return the original creature with one fewer revival counter",
            event_kind="step.begin", condition=END_STEP, once=True, source_object_id=item.source_object_id,
            source_logical_object_id=context["source_logical_object_id"],
            referred_object_ids=[row.object_id for row in arrivals],
            stack_template={"context": {"dynamic_effects": [_effect("return", count, [row.to_dict() for row in arrivals])]}},
        ).ref
    return ()


def _return_arrivals(host, item, source, effect, count, arrivals, reason):
    from ..zone_transitions import ZoneTransitionOwner

    original = item.context.get("dynamic_effects", ())
    expected = [_effect("return", count, [row.to_dict() for row in arrivals])]
    if (item.context.get("event") != "step.begin"
            or (host.state.phase, host.state.step) != (END_STEP["phase"], END_STEP["step"])
            or not item.context.get("delayed_trigger_ref")
            or FrozenMap({"effects": original}) != FrozenMap({"effects": expected})):
        raise ValueError("Revival return lost its sealed next-end-step instruction")
    cards = tuple(card for row in arrivals
                  if row.matches(card := host.state.cards.get(row.object_id)) and card.owner in host.active_seats
                  and host._type_parts(host._effective_card_data(card)["type_line"])[0] & PERMANENT_CARD_TYPES)
    if not cards:
        return ()
    counters = {card.object_id: (EffectEntryCounter("revival", count - 1, item.controller, source.stack_ref, "122.6a"),)
                for card in cards} if count > 1 else {}
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        tuple((card.object_id, "battlefield") for card in cards), reason=reason,
        destination_controllers={card.object_id: item.controller for card in cards},
        putting_players={card.object_id: item.controller for card in cards},
        effect_entry_counters=counters, replacement_selections=tuple(effect.get("_replacement_selections", ())),
        aura_target_refs=effect.get("_aura_target_refs"),
    )
    return tuple(card.ref for card in moved if card.zone == "battlefield")


def apply_revival_return(host, effect, *, actor, reason):
    try:
        count, arrivals = validate_resolved_revival_return(effect)
        source = ResolutionEffectSource.from_effect(effect)
        item = next((row for row in host.state.stack if row.ref == source.stack_ref), None)
        if (item is None or item.kind != "triggered_ability" or item.controller != actor
                or item.context.get("currently_resolving") is not True
                or not item.source_object_id or not item.context.get("source_logical_object_id")):
            raise ValueError("Revival return requires its original resolving trigger")
        if effect["phase"] == "schedule":
            return _schedule_return(host, item, count, arrivals)
        return _return_arrivals(host, item, source, effect, count, arrivals, reason)
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
