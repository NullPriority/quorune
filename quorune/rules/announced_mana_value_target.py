"""Bind one announced-X mana-value maximum to a public permanent target."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from typing import Any

from ..targets import target_plan


ANNOUNCED_MANA_VALUE_LIMIT = "announced_mana_value_limit"
ANNOUNCED_MANA_VALUE_TARGET_CAPABILITY = "target.mana_value.announced_cast_x"
OWN_GRAVEYARD_MANA_VALUE_CAPABILITY = "target.mana_value.announced_own_graveyard_x"
_CAST_X_LIMIT = {"schema_version": 1, "parameter": "x", "comparison": "at_most"}
_OWN_GRAVEYARD_X_LIMIT = {"schema_version": 2, "parameter": "x", "comparison": "at_most",
                          "scope": "own_graveyard_creature"}
_FIXED_LIMIT_FIELDS = {"mana_value", "mana_value_min", "mana_value_max", "mana_value_equal"}


def bind_announced_mana_value_limit(
    schema: Mapping[str, Any], announced_x: int,
) -> dict[str, Any]:
    """Materialize the target's normal fixed bound at the chosen X."""
    bound = deepcopy(dict(schema))
    if ANNOUNCED_MANA_VALUE_LIMIT not in bound:
        return bound
    limit = bound.pop(ANNOUNCED_MANA_VALUE_LIMIT)
    if (not isinstance(limit, Mapping) or dict(limit) not in (_CAST_X_LIMIT, _OWN_GRAVEYARD_X_LIMIT)
            or type(limit["schema_version"]) is not int
            or type(announced_x) is not int or announced_x < 0
            or _FIXED_LIMIT_FIELDS.intersection(bound)
            or {"groups", "modes", "count_parameter"}.intersection(bound)):
        raise ValueError("Invalid announced X mana-value target")

    bound["mana_value_max"] = announced_x
    plan = target_plan(bound, (), require_modes=False)
    if len(plan.groups) != 1:
        raise ValueError("Announced mana-value limit requires one target")
    target, = plan.groups
    if (target.min_targets, target.max_targets) != (1, 1):
        raise ValueError("Announced mana-value limit requires exactly one mandatory target")
    if limit["schema_version"] == 2:
        if (set(bound) != {"zones", "categories", "count", "owner_relation", "types_any", "mana_value_max"}
                or target.zones != ("graveyard",) or target.categories != ("card",)
                or target.owner_relation != "you" or target.types_any != ("creature",)):
            raise ValueError("The announced upper bound requires one owned graveyard creature card")
    elif target.zones != ("battlefield",) or target.categories != ("permanent",):
        raise ValueError("Announced mana-value limit requires a permanent target")
    return bound


def declare_announced_mana_value_limit(
    fixed_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Preserve an admitted fixed target shape while switching its upper bound to cast X."""
    declared = deepcopy(dict(fixed_schema))
    sample_bound = declared.pop("mana_value_max", None)
    if ANNOUNCED_MANA_VALUE_LIMIT in declared or type(sample_bound) is not int or sample_bound < 0:
        raise ValueError("An announced mana-value limit needs a closed fixed upper bound")
    declared[ANNOUNCED_MANA_VALUE_LIMIT] = dict(_CAST_X_LIMIT)
    bind_announced_mana_value_limit(declared, sample_bound)
    return declared


def declare_own_graveyard_mana_value_limit(fixed_schema: Mapping[str, Any]):
    target = deepcopy(dict(fixed_schema))
    amount = target.pop("mana_value_max", None)
    if ANNOUNCED_MANA_VALUE_LIMIT in target or type(amount) is not int or amount < 0:
        raise ValueError("An announced graveyard limit requires a closed fixed upper bound")
    target[ANNOUNCED_MANA_VALUE_LIMIT] = dict(_OWN_GRAVEYARD_X_LIMIT)
    bind_announced_mana_value_limit(target, amount)
    return target


def has_announced_mana_value_limit(schema: Any) -> bool:
    return isinstance(schema, Mapping) and ANNOUNCED_MANA_VALUE_LIMIT in schema


def announced_mana_value_capability(schema: Mapping[str, Any]) -> str:
    limit = schema.get(ANNOUNCED_MANA_VALUE_LIMIT)
    if isinstance(limit, Mapping) and dict(limit) == _OWN_GRAVEYARD_X_LIMIT:
        return OWN_GRAVEYARD_MANA_VALUE_CAPABILITY
    return ANNOUNCED_MANA_VALUE_TARGET_CAPABILITY


def project_announced_mana_value_target(effects, schema, mechanics):
    """Validate the target extension before the fixed result owners inspect it."""
    if not has_announced_mana_value_limit(schema) or len(effects) != 1:
        raise ValueError("The announced mana-value target requires one represented spell result")
    bound = bind_announced_mana_value_limit(schema, 0)
    if announced_mana_value_capability(schema) == OWN_GRAVEYARD_MANA_VALUE_CAPABILITY:
        from .reanimation_capability_shapes import fixed_target_reanimation_node_capabilities

        owns_result = fixed_target_reanimation_node_capabilities
    else:
        from .control_capability_shapes import fixed_control_node_capabilities
        from .node_capability_shapes import targeted_exile_node_capabilities

        operation = effects[0].get("op")
        if operation not in {"exile_permanent", "gain_control"}:
            raise ValueError("The battlefield limit requires its exact displacement result")
        owns_result = (targeted_exile_node_capabilities if operation == "exile_permanent"
                       else fixed_control_node_capabilities)
    if not owns_result(effects=effects, target_schema=bound, mechanic_ids=mechanics):
        raise ValueError("The announced mana-value limit requires a closed fixed target result")
    return bound


def project_announced_target_program(program):
    """Expose the existing fixed shape for admission and runtime dependency inference."""
    if (program.event != "resolve" or program.active_zone != "stack"
            or not program.ability_id.startswith("spell:")):
        raise ValueError("Announced mana-value targets require a resolving spell")
    return replace(
        program,
        target_schema=project_announced_mana_value_target(
            program.effects, program.target_schema, program.coverage,
        ),
    )


def announced_mana_value_node_capabilities(*, effects, target_schema, mechanics, cost_schema):
    from .capabilities import capability_dependencies_for_node

    try:
        fixed_schema = project_announced_mana_value_target(effects, target_schema, mechanics)
    except (TypeError, ValueError):
        return ()
    fixed = capability_dependencies_for_node(
        effects=effects, target_schema=fixed_schema, cost_schema=cost_schema,
        mechanic_ids=mechanics,
    )
    return tuple(sorted({announced_mana_value_capability(target_schema), *fixed}))
