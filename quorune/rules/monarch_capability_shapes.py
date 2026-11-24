from __future__ import annotations

"""Capability closure for fixed controller monarch designation."""

from typing import Any, Iterable, Mapping, Sequence

from ..compiler.monarch_templates import MONARCH_MECHANIC


MONARCH_DESIGNATION_CAPABILITY = "variant.monarch.designate"


def fixed_monarch_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize only the compiler-owned controller designation shape."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        MONARCH_MECHANIC not in mechanics
        or target_schema is not None
        or len(effects) != 1
    ):
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "player"}
        or effect.get("op") != "become_monarch"
        or effect.get("player") != "$controller"
    ):
        return ()
    return (MONARCH_DESIGNATION_CAPABILITY,)


def is_closed_fixed_monarch_program(program) -> bool:
    required = fixed_monarch_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return bool(required) and set(required) <= set(program.capability_dependencies)


__all__ = [
    "MONARCH_DESIGNATION_CAPABILITY",
    "fixed_monarch_node_capabilities",
    "is_closed_fixed_monarch_program",
]
