"""One original allocation followed by the existing controlled counter doubler."""

from collections.abc import Mapping

from ..monarch_counter_doubling_condition import (
    MONARCH_COUNTER_DISTRIBUTION_PREFIX_MECHANICS, MONARCH_COUNTER_DOUBLING_MECHANICS,
    is_monarch_counter_doubling, monarch_counter_doubling_guard, validate_monarch_counter_doubling,
)
from ..replacement.immutable import FrozenMap, thaw_value
from ..split_two_counter_target_amount import SplitTwoCounterTargetAmount
from .cumulative_object_upkeep import CONTROLLER_UPKEEP
from .effect_shape_walkers import nested_effects


MONARCH_COUNTER_DISTRIBUTION_CAPABILITY = "resolution.effect.monarch_counter_distribution_doubling"
MONARCH_COUNTER_DISTRIBUTION_MECHANICS = ("fixed-resolution-public-condition",)
_TARGETS = FrozenMap({"zones": ["battlefield"], "categories": ["permanent"],
                      "types_any": ["creature"], "up_to": 2})


def monarch_counter_distribution_target_schema():
    return thaw_value(_TARGETS)


def monarch_counter_distribution_effects():
    prefix = {"op": "place_counters_on_targets", "schema_version": 2, "cards": "$targets",
              "maximum_targets": 2, "counter": "+1/+1", "source": "$source",
              "amount": SplitTwoCounterTargetAmount(schema_version=2).to_dict()}
    return prefix, monarch_counter_doubling_guard()


def monarch_counter_distribution_capabilities(effects, target_schema):
    if (len(effects) != 2 or not isinstance(target_schema, Mapping)
            or type(target_schema.get("up_to")) is not int or FrozenMap(target_schema) != _TARGETS
            or not isinstance(effects[0], Mapping) or type(effects[0].get("maximum_targets")) is not int
            or FrozenMap(effects[0]) != FrozenMap(monarch_counter_distribution_effects()[0])
            or not is_monarch_counter_doubling(effects[1]) or effects[1].get("player") != "$controller"):
        raise ValueError("Monarch doubling requires the complete original optional two-counter allocation")
    _, doubling = validate_monarch_counter_doubling(effects[1])
    from .capabilities import capability_dependencies_for_node

    allocation = capability_dependencies_for_node(
        effects=effects[:1], target_schema=target_schema, mechanic_ids=MONARCH_COUNTER_DISTRIBUTION_PREFIX_MECHANICS,
    )
    named = capability_dependencies_for_node(
        effects=doubling, target_schema=None, mechanic_ids=MONARCH_COUNTER_DOUBLING_MECHANICS,
    )
    if not allocation or not named:
        raise ValueError("Monarch counter distribution needs independent allocation and doubling owners")
    return tuple(sorted({
        MONARCH_COUNTER_DISTRIBUTION_CAPABILITY, "resolution.effect.public_condition",
        "variant.monarch.designate", "trigger.effect.fixed_event", "trigger.placement.apnap",
        "counter.producer.fixed_event_trigger",
        *allocation, *named,
    }))


def monarch_counter_distribution_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((*program.effects, *program.handlers))
                   if is_monarch_counter_doubling(effect))
    if not marked and MONARCH_COUNTER_DISTRIBUTION_CAPABILITY not in program.capability_dependencies:
        return ()
    if ((program.event, program.active_zone) != ("step.begin", "battlefield")
            or program.handlers or program.cost_schema is not None
            or FrozenMap(program.event_condition or {}) != FrozenMap(CONTROLLER_UPKEEP)
            or len(marked) != 1 or tuple(program.effects[1:]) != marked):
        raise ValueError("Monarch counter distribution requires its original complete controller upkeep")
    return monarch_counter_distribution_capabilities(program.effects, program.target_schema)
