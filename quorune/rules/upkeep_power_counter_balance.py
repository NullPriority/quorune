"""Resolve the original upkeep source's power-dependent counter result."""

from collections.abc import Mapping

from ..counter_removal import CounterRemovalResult
from ..replacement.immutable import thaw_value
from .committed_quantity_followups import fixed_quantity_effect, quantity_followup_from_count
from .effect_shape_walkers import nested_effects


CAPABILITY = "counter.result.upkeep_source_power_balance"
FIELD = "upkeep_source_power_counter_balance"
REMOVAL_FOLLOWUP = "upkeep_power_removal_life_version"
COUNTER = "+1/+1"
REQUIREMENTS = (CAPABILITY, "counter.producer.fixed_effect", "counter.removal.fixed_effect",
                "life.change.effect", "trigger.event.scheduled_player_result", "trigger.placement.apnap")


def upkeep_power_counter_effect():
    return {"op": "remove_counters", "card": "$source.zone_object", "source": "$source",
            "counter": COUNTER, "amount": 1, FIELD: 1}


def has_upkeep_power_counter_effect(effect):
    return isinstance(effect, Mapping) and FIELD in effect


def upkeep_power_counter_node_capabilities(effects, target_schema):
    if len(effects) != 1 or target_schema is not None:
        return ()
    effect = effects[0]
    if (not has_upkeep_power_counter_effect(effect) or type(effect[FIELD]) is not int
            or type(effect.get("amount")) is not int or thaw_value(effect) != upkeep_power_counter_effect()):
        return ()
    return REQUIREMENTS


def upkeep_power_counter_runtime_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if has_upkeep_power_counter_effect(effect) or REMOVAL_FOLLOWUP in effect)
    if not marked and CAPABILITY not in program.capability_dependencies:
        return ()
    from .upkeep_hand_differences import controller_upkeep_hand_surplus_condition

    required = upkeep_power_counter_node_capabilities(program.effects, program.target_schema)
    if (not required or tuple(program.effects) != marked
            or (program.event, program.active_zone) != ("step.begin", "battlefield")
            or thaw_value(program.event_condition) != controller_upkeep_hand_surplus_condition()
            or program.cost_schema is not None or program.handlers or program.trigger_limit is not None):
        raise ValueError("Power-dependent counters require the original untargeted controller upkeep.")
    return required


def bind_upkeep_power_counter_balance(host, item, effect):
    program = host.semantics.get(item.semantic_key)
    context = item.context
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not upkeep_power_counter_runtime_dependencies(program)
            or not upkeep_power_counter_node_capabilities((effect,), None)
            or item.kind != "triggered_ability" or item.targets
            or context.get("currently_resolving") is not True
            or (context.get("event"), context.get("step"), context.get("player"))
            != ("step.begin", "upkeep", item.controller)):
        raise ValueError("Power-dependent counters lost their original upkeep source.")
    source = host.state.cards.get(item.source_object_id)
    if (source is None or source.zone != "battlefield" or source.phased_out
            or source.logical_object_id != context.get("source_logical_object_id")
            or item.controller not in host.active_seats):
        return None
    count = source.counters.get(COUNTER, 0)
    if not count:
        return None
    common = {"card": source.ref, "source": source.ref, "counter": COUNTER}
    power = host._numeric_stat(source.object_id, "power")
    if power <= 16:
        return {"op": "place_counters", **common, "amount": count}
    if count > 1:
        return {"op": "remove_counters", **common, "amount": count - 1, REMOVAL_FOLLOWUP: 1}
    return None


def life_after_upkeep_counter_removal(result):
    if not isinstance(result, CounterRemovalResult) or result.counter_name != COUNTER:
        raise ValueError("The life instruction requires the actual removed +1/+1 counters.")
    life = fixed_quantity_effect({"op": "life", "player": "$controller", "delta": 1})
    return quantity_followup_from_count(life, result.removed)
