"""Bind an ordinary Support X entry trigger to its original announced amount."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from ..entry_x_amount_model import (
    ENTRY_X_AMOUNT_CAPABILITY, ENTRY_X_AMOUNT_KIND, EntryXAmountSpec, resolve_entry_x_for_occurrence,
)
from ..replacement.immutable import FrozenMap, thaw_value


ENTRY_X_SUPPORT_LIMIT = "entry_x_support_limit"
ENTRY_X_SUPPORT_CAPABILITY = "counter.producer.support_entry_x"
ENTRY_X_SUPPORT_REQUIREMENTS = (ENTRY_X_AMOUNT_CAPABILITY, ENTRY_X_SUPPORT_CAPABILITY)
_SUPPORT_SCHEMA = FrozenMap({
    "zones": ["battlefield"],
    "categories": ["permanent"],
    "types_any": ["creature"],
    "source_exclusion": True,
    "support_source_context": "permanent",
})


def has_entry_x_support(schema):
    return isinstance(schema, Mapping) and ENTRY_X_SUPPORT_LIMIT in schema


def _uses_entry_x_support_effect(effect):
    maximum = effect.get("maximum_targets")
    return (effect.get("op") == "place_counters_on_targets" and isinstance(maximum, Mapping)
            and maximum.get("kind") == ENTRY_X_AMOUNT_KIND)


def declare_entry_x_support(fixed_effects, fixed_schema):
    effect, = fixed_effects
    if type(fixed_schema.get("up_to")) is not int or fixed_schema["up_to"] != 1:
        raise ValueError("Support X requires its fixed permanent-target shape")
    schema = dict(fixed_schema)
    del schema["up_to"]
    amount = EntryXAmountSpec().to_dict()
    schema[ENTRY_X_SUPPORT_LIMIT] = amount
    return ({**effect, "maximum_targets": amount},), schema


def _bound_support_schema(schema, maximum):
    if type(maximum) is not int or maximum < 0:
        raise ValueError("Support X needs a nonnegative original entry amount")
    if not isinstance(schema, Mapping):
        raise ValueError("Support X requires its versioned target schema")
    ordinary = dict(schema)
    amount = ordinary.pop(ENTRY_X_SUPPORT_LIMIT, None)
    if (EntryXAmountSpec.from_dict(amount) != EntryXAmountSpec()
            or ordinary.get("source_exclusion") is not True or FrozenMap(ordinary) != _SUPPORT_SCHEMA):
        raise ValueError("Support X targets only other battlefield creatures")
    return {**thaw_value(_SUPPORT_SCHEMA), "up_to": maximum}


def _project_support(effects, target_schema, mechanics):
    from .node_capability_shapes import fixed_counter_placement_target_set_node_capabilities

    effect, = effects
    if (not _uses_entry_x_support_effect(effect)
            or EntryXAmountSpec.from_dict(effect["maximum_targets"]) != EntryXAmountSpec()):
        raise ValueError("Support X places one +1/+1 counter using its entry-bound target limit")
    fixed_effects = ({**effect, "maximum_targets": 1},)
    fixed_schema = _bound_support_schema(target_schema, 1)
    dependencies = fixed_counter_placement_target_set_node_capabilities(
        effects=fixed_effects, target_schema=fixed_schema, mechanic_ids=mechanics,
    )
    if "counter.producer.support" not in dependencies:
        raise ValueError("Support X requires the canonical Support transaction")
    return fixed_effects, fixed_schema, dependencies


def entry_x_support_node_capabilities(*, effects, target_schema, mechanics, cost_schema):
    if cost_schema is not None:
        return ()
    try:
        _, _, fixed = _project_support(effects, target_schema, mechanics)
    except (TypeError, ValueError):
        return ()
    return (*ENTRY_X_SUPPORT_REQUIREMENTS, *fixed)


def project_entry_x_support_program(program):
    if (program.event != "permanent.enter.self" or program.active_zone != "battlefield"
            or program.cost_schema is not None or program.handlers):
        raise ValueError("Support X requires its own ordinary battlefield entry")
    effects, schema, _ = _project_support(program.effects, program.target_schema, program.coverage)
    return replace(program, effects=effects, target_schema=schema)


def closed_entry_x_support_program(program, *, body_is_closed):
    if not set(ENTRY_X_SUPPORT_REQUIREMENTS).issubset(program.capability_dependencies):
        return False
    try:
        fixed = project_entry_x_support_program(program)
    except (TypeError, ValueError):
        return False
    return body_is_closed(fixed)


def entry_x_support_trigger_context(program, source, context):
    schema = program.target_schema
    if not has_entry_x_support(schema):
        return {}
    maximum = resolve_entry_x_for_occurrence(
        schema[ENTRY_X_SUPPORT_LIMIT], source_object_id=source.object_id, context=context,
    )
    result = {"target_schema_override": _bound_support_schema(schema, maximum)}
    if maximum == 0:
        result.update(trigger_target_selection_pending=False, targets_chosen_at_creation=True)
    return result


def entry_x_support_has_no_targets(item, raw_effect, effect):
    """Validate that resolution and ordinary target placement use the same limit."""
    if not _uses_entry_x_support_effect(raw_effect):
        return False
    maximum = effect.get("maximum_targets")
    schema = item.context.get("target_schema_override")
    if (type(maximum) is not int or maximum < 0 or not isinstance(schema, Mapping)
            or type(schema.get("up_to")) is not int
            or schema.get("source_exclusion") is not True
            or FrozenMap(schema) != FrozenMap({**thaw_value(_SUPPORT_SCHEMA), "up_to": maximum})
            or len(item.targets) > maximum):
        raise ValueError("Support X lost its original entry-bound target limit")
    return not item.targets
