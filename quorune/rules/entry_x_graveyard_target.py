"""Use a sealed self-entry amount to bind one owned public graveyard target."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace

from ..entry_x_amount_model import (
    ENTRY_X_AMOUNT_CAPABILITY, EntryXAmountSpec, resolve_entry_x_for_occurrence,
)
from ..targets import target_plan


ENTRY_X_TARGET_LIMIT = "entry_x_mana_value_limit"
ENTRY_X_GRAVEYARD_TARGET_CAPABILITY = "target.mana_value.sealed_entry_x_graveyard"
ENTRY_X_GRAVEYARD_REQUIREMENTS = (ENTRY_X_AMOUNT_CAPABILITY, ENTRY_X_GRAVEYARD_TARGET_CAPABILITY)


def has_entry_x_target_limit(schema):
    return isinstance(schema, Mapping) and ENTRY_X_TARGET_LIMIT in schema


def declare_entry_x_graveyard_target(fixed_schema: Mapping) -> dict:
    schema = deepcopy(dict(fixed_schema))
    bound = schema.pop("mana_value_max", None)
    if type(bound) is not int or bound < 0:
        raise ValueError("Entry X needs the fixed graveyard limit it replaces")
    schema[ENTRY_X_TARGET_LIMIT] = {"schema_version": 1, "ceiling": EntryXAmountSpec().to_dict()}
    bind_entry_x_graveyard_target(schema, bound)
    return schema


def bind_entry_x_graveyard_target(schema: Mapping, bound: int) -> dict:
    result = deepcopy(dict(schema))
    amount = result.pop(ENTRY_X_TARGET_LIMIT, None)
    if (not isinstance(amount, Mapping) or set(amount) != {"schema_version", "ceiling"}
            or type(amount["schema_version"]) is not int or amount["schema_version"] != 1
            or EntryXAmountSpec.from_dict(amount["ceiling"]) != EntryXAmountSpec()
            or type(bound) is not int or bound < 0
            or set(result) != {"zones", "categories", "owner_relation", "types_any", "count"}):
        raise ValueError("Entry X requires its exact declared graveyard target")
    result["mana_value_max"] = bound
    target, = target_plan(result).groups
    if (target.zones != ("graveyard",) or target.owner_relation != "you"
            or target.categories != ("card",) or target.types_any != ("artifact", "creature")
            or (target.min_targets, target.max_targets) != (1, 1)):
        raise ValueError("Entry X supports one owned artifact or creature card")
    return result



def entry_x_graveyard_trigger_context(program, source, context):
    """Freeze the target ceiling from the source's own already-committed entry."""
    schema = program.target_schema
    if not has_entry_x_target_limit(schema):
        return {}
    limit = schema[ENTRY_X_TARGET_LIMIT]["ceiling"]
    bound = resolve_entry_x_for_occurrence(limit, source_object_id=source.object_id, context=context)
    return {"target_schema_override": bind_entry_x_graveyard_target(schema, bound)}


def _project_target(effects, target_schema, mechanics):
    from .reanimation_capability_shapes import fixed_target_reanimation_node_capabilities

    bound = bind_entry_x_graveyard_target(target_schema, 0)
    expected = {"op": "reanimate", "card": "$target.0", "controller": "$controller", "tapped": False}
    if tuple(effects) != (expected,) or not fixed_target_reanimation_node_capabilities(
            effects=effects, target_schema=bound, mechanic_ids=mechanics):
        raise ValueError("Entry X requires its exact untapped return under the trigger controller")
    return bound


def project_entry_x_graveyard_target_program(program):
    if program.event != "permanent.enter.self" or program.active_zone != "battlefield" or program.cost_schema:
        raise ValueError("Entry X targets require their own battlefield entry")
    return replace(program, target_schema=_project_target(program.effects, program.target_schema, program.coverage))


def entry_x_graveyard_target_node_capabilities(*, effects, target_schema, mechanics, cost_schema):
    from .capabilities import capability_dependencies_for_node

    try:
        bound = _project_target(effects, target_schema, mechanics)
    except (ValueError, TypeError, KeyError):
        return ()
    fixed = capability_dependencies_for_node(
        effects=effects, target_schema=bound, cost_schema=cost_schema, mechanic_ids=mechanics,
    )
    return tuple(sorted({*ENTRY_X_GRAVEYARD_REQUIREMENTS, *fixed}))


def closed_entry_x_graveyard_target_program(program, *, body_is_closed):
    if not set(ENTRY_X_GRAVEYARD_REQUIREMENTS).issubset(program.capability_dependencies):
        return False
    try:
        fixed = project_entry_x_graveyard_target_program(program)
    except (ValueError, TypeError, KeyError):
        return False
    return body_is_closed(fixed)
