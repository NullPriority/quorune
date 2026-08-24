from __future__ import annotations

"""Capability-closed recognition for generated fixed modal programs."""

from typing import Mapping

from ..rules.capabilities import capability_dependencies_for_node
from ..rules.modal_capability_shapes import FIXED_MODAL_WRAPPER_MECHANICS
from ..semantics import SemanticProgram
from .modal_templates import (
    FIXED_CHOOSE_ONE_MODAL_MECHANIC,
    FIXED_NONREPEATING_MODAL_MECHANIC,
)


def is_closed_fixed_modal_program(program: SemanticProgram) -> bool:
    """Recognize one strict modal program and every typed branch owner."""

    coverage = tuple(str(value) for value in program.coverage)
    legacy = FIXED_CHOOSE_ONE_MODAL_MECHANIC in coverage
    expanded = FIXED_NONREPEATING_MODAL_MECHANIC in coverage
    if legacy == expanded:
        return False
    target_schema = program.target_schema
    definitions = (
        target_schema.get("modes")
        if isinstance(target_schema, Mapping)
        else None
    )
    if not isinstance(definitions, Mapping):
        return False
    mechanics: list[str] = [
        (
            FIXED_CHOOSE_ONE_MODAL_MECHANIC
            if legacy
            else FIXED_NONREPEATING_MODAL_MECHANIC
        )
    ]
    for definition in definitions.values():
        branch_mechanics = (
            definition.get("mechanics")
            if isinstance(definition, Mapping)
            else None
        )
        if (
            not isinstance(branch_mechanics, (list, tuple))
            or len(branch_mechanics) != len(set(branch_mechanics))
        ):
            return False
        mechanics.extend(
            str(mechanic)
            for mechanic in branch_mechanics
            if mechanic not in mechanics
        )
    mechanics.extend(
        mechanic
        for mechanic in coverage
        if mechanic in FIXED_MODAL_WRAPPER_MECHANICS
        and mechanic not in mechanics
    )
    required = set(
        capability_dependencies_for_node(
            effects=program.effects,
            target_schema=target_schema,
            mechanic_ids=mechanics,
            cost_schema=program.cost_schema,
        )
    )
    return bool(required) and required == set(
        program.capability_dependencies
    )


__all__ = ["is_closed_fixed_modal_program"]
