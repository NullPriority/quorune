from __future__ import annotations

"""Closure of flattened effects sharing an already established reference."""

from typing import Any, Iterable, Mapping, Sequence

from ..compiler.bound_effect_programs import (
    BOUND_EFFECT_PROGRAM_CAPABILITY, BOUND_EFFECT_PROGRAM_MECHANIC,
)
from .fixed_effect_clause_shapes import closed_effect_component_capabilities, _contains_target_reference
from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed


def _target_zero_only(value: Any) -> bool:
    if isinstance(value, str) and value.startswith("$target"):
        return value in {"$target.0", "$target.current_controller.0"}
    if isinstance(value, Mapping):
        return all(_target_zero_only(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return all(_target_zero_only(child) for child in value)
    return True


def bound_effect_program_node_capabilities(
    *, effects: Sequence[Mapping[str, Any]], target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    mechanics = set(mechanic_ids)
    if BOUND_EFFECT_PROGRAM_MECHANIC not in mechanics or not 2 <= len(effects) <= 8:
        return ()
    if target_schema is not None:
        if type(target_schema.get("count")) is not int or target_schema.get("count") != 1:
            return ()
        player_schema = {
            "zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 1,
        }
        if dict(target_schema) not in (player_schema, {**player_schema, "player_relation": "opponent"}):
            if not direct_permanent_target_schema_is_closed(target_schema):
                return ()
    target_seen = False
    dependencies = {BOUND_EFFECT_PROGRAM_CAPABILITY, "resolution.effect_program.closed_components"}
    for effect in effects:
        if not isinstance(effect, Mapping) or not _target_zero_only(effect):
            return ()
        targeted = _contains_target_reference(effect)
        if targeted and target_schema is None:
            return ()
        target_seen |= targeted
        component = closed_effect_component_capabilities(
            (effect,), target_schema=target_schema if targeted else None, mechanics=mechanics,
        )
        if not component:
            return ()
        dependencies.update(component)
    if target_seen != (target_schema is not None):
        return ()
    return tuple(sorted(dependencies))


__all__ = ["bound_effect_program_node_capabilities"]
