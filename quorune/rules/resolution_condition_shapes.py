from __future__ import annotations

"""Full child-shape closure for controller-bound conditional programs."""

from typing import Any, Iterable, Mapping, Sequence

from ..resolution_conditions import (
    RESOLUTION_CONDITION_CAPABILITY,
    RESOLUTION_CONDITION_MECHANIC,
    RESOLUTION_CONDITION_OPERATION,
    validate_resolution_condition_instruction,
)
from .closed_effect_program_shapes import _contains_target_reference


def resolution_condition_node_capabilities(
    *, effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
    cost_schema: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    from .capabilities import capability_dependencies_for_node

    mechanics = set(mechanic_ids)
    if RESOLUTION_CONDITION_MECHANIC not in mechanics or not effects:
        return ()
    wrapper = effects[-1]
    try:
        _condition, conditional = validate_resolution_condition_instruction(wrapper)
    except (ValueError, TypeError, KeyError):
        return ()
    if wrapper["player"] != "$controller":
        return ()
    prefix = effects[:-1]
    if _contains_target_reference(conditional):
        return ()
    if len(prefix) + len(conditional) > 8 or any(
        effect.get("op") == RESOLUTION_CONDITION_OPERATION for effect in prefix
    ):
        return ()
    branches = tuple(branch for branch in (prefix, conditional) if branch)
    if bool(prefix) != bool(wrapper["prefix_mechanic_ids"]) or not set((*wrapper["prefix_mechanic_ids"], *wrapper["mechanic_ids"])) <= mechanics:
        return ()
    targeted = tuple(branch for branch in branches if _contains_target_reference(branch))
    if len(targeted) > 1 or (target_schema is not None) != bool(targeted):
        return ()
    dependencies = {RESOLUTION_CONDITION_CAPABILITY}
    ambient_mechanics = mechanics - {
        RESOLUTION_CONDITION_MECHANIC,
        *wrapper["prefix_mechanic_ids"],
        *wrapper["mechanic_ids"],
    }
    for branch in branches:
        branch_mechanics = wrapper["prefix_mechanic_ids"] if branch is prefix else wrapper["mechanic_ids"]
        child = capability_dependencies_for_node(
            effects=branch,
            target_schema=target_schema if branch in targeted else None,
            mechanic_ids=(*branch_mechanics, *ambient_mechanics),
            cost_schema=cost_schema,
        )
        if not child:
            return ()
        dependencies.update(child)
    return tuple(sorted(dependencies))
