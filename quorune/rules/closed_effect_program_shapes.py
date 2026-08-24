from __future__ import annotations

"""Capability closure for bounded programs of mandatory typed effects."""

from typing import Any, Iterable, Mapping, Sequence

from .fixed_effect_clause_shapes import (
    closed_effect_component_capabilities,
)


CLOSED_EFFECT_PROGRAM_MECHANIC = "closed-effect-program"
CLOSED_EFFECT_PROGRAM_CAPABILITY = (
    "resolution.effect_program.closed_components"
)


def _contains_target_reference(value: Any) -> bool:
    if isinstance(value, str):
        return value in {"$target", "$targets"} or value.startswith("$target.")
    if isinstance(value, Mapping):
        return any(_contains_target_reference(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_target_reference(child) for child in value)
    return False


def closed_effect_program_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Own a bounded ordered program when every flattened effect is closed."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        CLOSED_EFFECT_PROGRAM_MECHANIC not in mechanics
        or not 2 <= len(effects) <= 8
    ):
        return ()
    target_effects = tuple(
        effect for effect in effects if _contains_target_reference(effect)
    )
    if target_schema is None:
        if target_effects:
            return ()
    elif not target_effects or target_schema.get("count") != 1:
        return ()

    component_mechanics = mechanics - {CLOSED_EFFECT_PROGRAM_MECHANIC}
    dependencies = {CLOSED_EFFECT_PROGRAM_CAPABILITY}
    if target_effects:
        target_dependencies = closed_effect_component_capabilities(
            target_effects,
            target_schema=target_schema,
            mechanics=component_mechanics,
        )
        if not target_dependencies:
            return ()
        dependencies.update(target_dependencies)
    for effect in effects:
        if _contains_target_reference(effect):
            continue
        component = closed_effect_component_capabilities(
            (effect,),
            target_schema=None,
            mechanics=component_mechanics,
        )
        if not component:
            return ()
        dependencies.update(component)
    return tuple(sorted(dependencies))


__all__ = [
    "CLOSED_EFFECT_PROGRAM_CAPABILITY",
    "CLOSED_EFFECT_PROGRAM_MECHANIC",
    "closed_effect_program_node_capabilities",
]
