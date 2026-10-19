from __future__ import annotations

"""Full child-shape closure for controller-bound conditional programs."""

from typing import Any, Iterable, Mapping, Sequence

from ..resolution_conditions import (
    RESOLUTION_CONDITION_CAPABILITY,
    RESOLUTION_CONDITION_MECHANIC,
    RESOLUTION_CONDITION_OPERATION,
    validate_resolution_condition_instruction,
)
from ..continuous_conditions import FixedPublicStateConditionKind
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
    otherwise=wrapper.get('otherwise_effects',())
    if _contains_target_reference(conditional) or _contains_target_reference(otherwise):
        return ()
    if len(prefix) + max(len(conditional),len(otherwise)) > 8 or any(
        effect.get("op") == RESOLUTION_CONDITION_OPERATION for effect in prefix
    ):
        return ()
    branches = tuple((branch,ids) for branch,ids in ((prefix,wrapper['prefix_mechanic_ids']),
        (conditional,wrapper['mechanic_ids']),(otherwise,wrapper.get('otherwise_mechanic_ids',()))) if branch)
    if bool(prefix) != bool(wrapper["prefix_mechanic_ids"]) or not set((*wrapper["prefix_mechanic_ids"], *wrapper["mechanic_ids"],*wrapper.get('otherwise_mechanic_ids',()))) <= mechanics:
        return ()
    targeted = tuple(branch for branch,_ids in branches if _contains_target_reference(branch))
    if len(targeted) > 1 or (target_schema is not None) != bool(targeted):
        return ()
    dependencies = {RESOLUTION_CONDITION_CAPABILITY}
    from ..characteristic_fragments import characteristic_quantity_capabilities
    dependencies.update(characteristic_quantity_capabilities(_condition.to_dict()))
    if wrapper.get('schema_version') in {3,4}:
        from ..resolution_conditions import PUBLIC_SELF_REPLACEMENT_CAPABILITY
        dependencies.add(PUBLIC_SELF_REPLACEMENT_CAPABILITY)
    if _condition.kind == FixedPublicStateConditionKind.CONTROLLER_CREATURE_NUMERIC_EXISTS:
        dependencies.add('condition.creature.fixed_numeric_exists')
    if wrapper.get('schema_version') in {2,4}:
        from ..resolution_conditions import CAST_FACT_CONDITION_CAPABILITY, CAST_FACT_CONDITION_MECHANIC
        if CAST_FACT_CONDITION_MECHANIC not in mechanics or wrapper['cast_fact'] != '$context.kicked':
            return ()
        dependencies.add(CAST_FACT_CONDITION_CAPABILITY)
    ambient_mechanics = mechanics - {
        RESOLUTION_CONDITION_MECHANIC,
        'fixed-cast-fact-condition',
        *wrapper["prefix_mechanic_ids"],
        *wrapper["mechanic_ids"],
        *wrapper.get('otherwise_mechanic_ids',()),
    }
    for branch,branch_mechanics in branches:
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
