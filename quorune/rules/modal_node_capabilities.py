from __future__ import annotations

"""Independent branch-by-branch modal capability traversal."""

from typing import Any, Iterable, Mapping, Sequence

from ..compiler.modal_templates import (
    FIXED_CHOOSE_ONE_MODAL_MECHANIC, FIXED_NONREPEATING_MODAL_MECHANIC,
    FIXED_CHOOSE_ONE_MODAL_CAPABILITY, FIXED_NONREPEATING_MODAL_CAPABILITY,
)
from .modal_capability_shapes import fixed_choose_one_modal_branches, fixed_nonrepeating_modal_branches


def fixed_modal_capability_dependencies(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanics: Iterable[str],
) -> tuple[str, ...]:
    from .capabilities import capability_dependencies_for_node, capability_covered_mechanics, MECHANIC_CAPABILITY_DEPENDENCIES

    mechanic_values = tuple(str(value).casefold() for value in mechanics)
    nonrepeating = FIXED_NONREPEATING_MODAL_MECHANIC in mechanic_values
    branches = (
        fixed_nonrepeating_modal_branches(
            effects=effects,
            target_schema=target_schema,
            mechanic_ids=mechanic_values,
        )
        if nonrepeating
        else fixed_choose_one_modal_branches(
            effects=effects,
            target_schema=target_schema,
            mechanic_ids=mechanic_values,
        )
    )
    if branches is None:
        return ()
    modal_mechanic = (
        FIXED_NONREPEATING_MODAL_MECHANIC
        if nonrepeating
        else FIXED_CHOOSE_ONE_MODAL_MECHANIC
    )
    dependencies = {
        (
            FIXED_NONREPEATING_MODAL_CAPABILITY
            if nonrepeating
            else FIXED_CHOOSE_ONE_MODAL_CAPABILITY
        )
    }
    represented_mechanics = {modal_mechanic}
    for branch in branches:
        branch_dependencies = capability_dependencies_for_node(
            effects=branch.effects,
            target_schema=branch.target_schema,
            mechanic_ids=branch.mechanics,
        )
        if not branch_dependencies or (
            set(branch.mechanics)
            - set(capability_covered_mechanics(branch_dependencies))
        ):
            return ()
        dependencies.update(branch_dependencies)
        represented_mechanics.update(branch.mechanics)
    outer_mechanics = set(mechanic_values) - represented_mechanics
    for mechanic in outer_mechanics:
        if mechanic == "cr-603-handling-triggered-abilities":
            dependencies.add("trigger.placement.apnap")
            continue
        required = MECHANIC_CAPABILITY_DEPENDENCIES.get(mechanic)
        if not required:
            return ()
        dependencies.update(required)
    return tuple(sorted(dependencies))
