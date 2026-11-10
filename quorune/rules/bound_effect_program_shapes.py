from __future__ import annotations

"""Closure of flattened effects sharing an already established reference."""

from typing import Any, Iterable, Mapping, Sequence

from ..compiler.bound_effect_programs import (
    BOUND_EFFECT_PROGRAM_CAPABILITY, BOUND_EFFECT_PROGRAM_MECHANIC, CLOSED_EFFECT_PROGRAM_MECHANIC,
    target_controller_prefix_matches, target_controller_search_prefix_matches,
)
from ..compiler.optional_effect_templates import OPTIONAL_EFFECT_OPERATION
from ..target_controller_information import (
    TARGET_CONTROLLER_INFORMATION_CAPABILITY, TARGET_CONTROLLER_INFORMATION_KIND,
    TARGET_CONTROLLER_INFORMATION_MECHANIC, controller_reference_index,
)
from .damage_capability_shapes import fixed_damage_node_capabilities
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


def has_target_controller_recipient(effect: Mapping[str, Any]) -> bool:
    recipient = effect.get("target") if isinstance(effect, Mapping) else None
    return isinstance(recipient, Mapping) and recipient.get("kind") == TARGET_CONTROLLER_INFORMATION_KIND


def _target_controller_pair_dependencies(effects, schema, mechanics) -> tuple[str, ...]:
    if (
        CLOSED_EFFECT_PROGRAM_MECHANIC not in mechanics
        or schema is None or schema.get("up_to") or not direct_permanent_target_schema_is_closed(schema)
        or len(effects) != 2 or not has_target_controller_recipient(effects[1])
        or not target_controller_prefix_matches(effects[0]) or not _target_zero_only(effects[0])
    ):
        return ()
    prefix = closed_effect_component_capabilities(
        effects[:1], target_schema=schema, mechanics=mechanics,
    )
    damage = fixed_damage_node_capabilities(
        effects=effects[1:], target_schema=None, mechanic_ids=mechanics,
    )
    return (*prefix, *damage) if prefix and damage else ()


def has_target_controller_search_role(effect: Mapping[str, Any]) -> bool:
    if not isinstance(effect, Mapping) or effect.get("op") != OPTIONAL_EFFECT_OPERATION:
        return False
    nested = effect.get("effects")
    search_player = (
        nested[0].get("searching_player")
        if isinstance(nested, (list, tuple)) and len(nested) == 1 and isinstance(nested[0], Mapping)
        else None
    )
    return any(isinstance(role, Mapping) and role.get("kind") == TARGET_CONTROLLER_INFORMATION_KIND
               for role in (effect.get("player"), search_player))


def _target_controller_search_dependencies(effects, schema, mechanics) -> tuple[str, ...]:
    if (
        not {CLOSED_EFFECT_PROGRAM_MECHANIC, TARGET_CONTROLLER_INFORMATION_MECHANIC} <= mechanics
        or schema is None or schema.get("up_to") or not direct_permanent_target_schema_is_closed(schema)
        or not target_controller_search_prefix_matches(effects[0]) or not _target_zero_only(effects[0])
        or any(_contains_target_reference(effect) or has_target_controller_recipient(effect)
               or has_target_controller_search_role(effect) for effect in effects[2:])
    ):
        return ()
    wrapper = effects[1]
    nested = wrapper.get("effects")
    if (
        set(wrapper) != {"op", "player", "effects"} or wrapper["op"] != OPTIONAL_EFFECT_OPERATION
        or not isinstance(nested, (list, tuple)) or len(nested) != 1
        or not isinstance(nested[0], Mapping) or nested[0].get("op") != "search"
    ):
        return ()
    search = dict(nested[0])
    try:
        if controller_reference_index(wrapper["player"]) != 0 or controller_reference_index(search.pop("searching_player")) != 0:
            return ()
    except (KeyError, ValueError):
        return ()
    if search.get("destination") != "battlefield" or search.get("count") != {"minimum": 1, "maximum": 1}:
        return ()
    restored = {**wrapper, "player": "$controller", "effects": [search]}
    parts = (
        closed_effect_component_capabilities(effects[:1], target_schema=schema, mechanics=mechanics),
        closed_effect_component_capabilities((restored,), target_schema=None, mechanics=mechanics),
        *(closed_effect_component_capabilities((effect,), target_schema=None, mechanics=mechanics)
          for effect in effects[2:]),
    )
    if not all(parts):
        return ()
    return (TARGET_CONTROLLER_INFORMATION_CAPABILITY, *(dependency for part in parts for dependency in part))


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
    if has_target_controller_search_role(effects[1]):
        search = _target_controller_search_dependencies(effects, target_schema, mechanics)
        return tuple(sorted(dependencies.union(search))) if search else ()
    if any(has_target_controller_recipient(effect) for effect in effects):
        pair = _target_controller_pair_dependencies(effects, target_schema, mechanics)
        return tuple(sorted(dependencies.union(pair))) if pair else ()
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
