"""Closed rules envelope for an upkeep allocation of existing source counters."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from .cumulative_object_upkeep import CONTROLLER_UPKEEP


SOURCE_COUNTER_DISTRIBUTION_OPERATION = "offer_source_counter_distribution"
SOURCE_COUNTER_DISTRIBUTION_CAPABILITY = "counter.producer.source_upkeep_distribution"
SOURCE_COUNTER_TRANSFER_CAPABILITY = "counter.producer.source_upkeep_targeted_transfer"
SOURCE_COUNTER_DISTRIBUTION_MECHANICS = ("cr-122-counters",)
SOURCE_COUNTER_TRANSFER_MECHANICS = (*SOURCE_COUNTER_DISTRIBUTION_MECHANICS, "cr-115-targets")
SOURCE_COUNTER_DISTRIBUTION_REQUIREMENTS = (
    SOURCE_COUNTER_DISTRIBUTION_CAPABILITY,
    "counter.placement.quantity_replacement",
    "counter.producer.fixed_effect",
    "trigger.effect.fixed_event",
    "trigger.placement.apnap",
)


def source_counter_distribution_effect():
    return {
        "op": SOURCE_COUNTER_DISTRIBUTION_OPERATION,
        "schema_version": 1,
        "player": "$controller",
        "source": "$source.zone_object",
    }


def source_counter_transfer_effect():
    return {**source_counter_distribution_effect(), "schema_version": 2, "target": "$target.0"}


def source_counter_transfer_target_schema():
    return {
        "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
        "count": 1, "source_exclusion": True,
    }


def uses_source_counter_distribution(effect):
    return effect.get("op") == SOURCE_COUNTER_DISTRIBUTION_OPERATION


def source_counter_distribution_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema):
    if len(effects) != 1 or cost_schema is not None:
        return ()
    effect, = effects
    if type(effect.get("schema_version")) is not int:
        return ()
    if (target_schema is None and dict(effect) == source_counter_distribution_effect()
            and set(SOURCE_COUNTER_DISTRIBUTION_MECHANICS).issubset(mechanic_ids)):
        return SOURCE_COUNTER_DISTRIBUTION_REQUIREMENTS
    if (dict(effect) == source_counter_transfer_effect() and isinstance(target_schema, Mapping)
            and type(target_schema.get("count")) is int and target_schema.get("source_exclusion") is True
            and FrozenMap(target_schema) == FrozenMap(source_counter_transfer_target_schema())
            and set(SOURCE_COUNTER_TRANSFER_MECHANICS).issubset(mechanic_ids)):
        return (*SOURCE_COUNTER_DISTRIBUTION_REQUIREMENTS, SOURCE_COUNTER_TRANSFER_CAPABILITY,
                "target.revalidate_resolution")
    return ()


def source_counter_distribution_program_dependencies(program, *, marked):
    effects = tuple(program.effects)
    if (effects != marked or len(marked) != 1 or program.handlers
            or (program.event, program.active_zone) != ("step.begin", "battlefield")
            or FrozenMap(program.event_condition or {}) != FrozenMap(CONTROLLER_UPKEEP)):
        raise ValueError("Source counter distribution requires its original controller upkeep")
    required = source_counter_distribution_node_capabilities(
        effects=effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        cost_schema=program.cost_schema,
    )
    if not required:
        raise ValueError("Source counter distribution requires its complete instruction and recipient scope")
    return required
