from __future__ import annotations

"""Capability shape for closed target-hand inspection programs."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..replacement.immutable import FrozenMap, thaw_value

from ..compiler.hand_inspection_templates import (
    FIXED_HAND_INSPECTION_CAPABILITY,
    FIXED_HAND_INSPECTION_MECHANIC,
)
from .hand_inspection import (
    DEFENDING_HAND_OBSERVATION_CAPABILITY,
    DEFENDING_HAND_OBSERVATION_MECHANIC,
    FIXED_HAND_INSPECTION_OPERATION,
    SELECTED_HAND_REVEAL_DISCARD_CAPABILITY,
    selected_hand_reveal_discard_effect,
    HandCardPredicateSpec,
    HandInspectionAction,
    HandInspectionError,
    HandInspectionMode,
    is_defending_hand_observation,
)
from .fixed_controller_effect_shapes import fixed_life_node_capabilities
from .node_capability_shapes import fixed_scry_node_capabilities


def selected_hand_reveal_discard_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(effect.get("inspection") == HandInspectionMode.REVEAL_SELECTED_THREE.value
               for effect in nested_effects((program.effects, program.handlers))):
        return ()
    scope = fixed_hand_inspection_node_capabilities(
        effects=program.effects, target_schema=thaw_value(FrozenMap(program.target_schema)) if program.target_schema else None,
        mechanic_ids=program.coverage,
    )
    if (SELECTED_HAND_REVEAL_DISCARD_CAPABILITY not in scope
            or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:") or program.handlers
            or program.event_condition is not None or program.cost_schema is not None):
        raise ValueError("Selected hand reveal requires the original target and complete resolving spell")
    return scope


def fixed_hand_inspection_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize one inspected target hand with an optional fixed tail."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if DEFENDING_HAND_OBSERVATION_MECHANIC in mechanics:
        if (target_schema is None and "cr-402-hand" in mechanics and len(effects) == 1
                and is_defending_hand_observation(effects[0])):
            return (DEFENDING_HAND_OBSERVATION_CAPABILITY,)
        return ()
    if (
        not {
            FIXED_HAND_INSPECTION_MECHANIC,
            "cr-402-hand",
            "cr-115-targets",
        }.issubset(mechanics)
        or len(effects) not in {1, 2}
        or dict(target_schema or {})
        not in (
            {
                "zones": ["player"],
                "categories": ["player"],
                "player_relation": "any",
                "count": 1,
            },
            {
                "zones": ["player"],
                "categories": ["player"],
                "player_relation": "opponent",
                "count": 1,
            },
        )
    ):
        return ()
    effect = effects[0]
    if effect.get("inspection") == HandInspectionMode.REVEAL_SELECTED_THREE.value:
        if (len(effects) != 1 or target_schema.get("player_relation") != "any"
                or type(target_schema.get("count")) is not int
                or thaw_value(FrozenMap(effect)) != selected_hand_reveal_discard_effect()):
            return ()
        return (FIXED_HAND_INSPECTION_CAPABILITY, SELECTED_HAND_REVEAL_DISCARD_CAPABILITY,
                "target.revalidate_resolution", "zone.change.destination_replacement")
    if (
        set(effect)
        != {
            "op",
            "player",
            "target",
            "inspection",
            "action",
            "predicate",
        }
        or effect.get("op") != FIXED_HAND_INSPECTION_OPERATION
        or effect.get("player") != "$controller"
        or effect.get("target") != "$target.0"
    ):
        return ()
    try:
        mode = HandInspectionMode(str(effect.get("inspection") or ""))
        action = HandInspectionAction(str(effect.get("action") or ""))
        predicate = (
            None
            if effect.get("predicate") is None
            else HandCardPredicateSpec.from_dict(effect["predicate"])
        )
    except (HandInspectionError, TypeError, ValueError):
        return ()
    if (action == HandInspectionAction.OBSERVE) != (predicate is None):
        return ()
    if (
        action == HandInspectionAction.OBSERVE and mode == HandInspectionMode.REVEAL
        and target_schema.get("player_relation") != "any"
    ):
        return ()
    if (
        action == HandInspectionAction.DISCARD_ALL
        and mode != HandInspectionMode.REVEAL
    ):
        return ()
    dependencies = {
        FIXED_HAND_INSPECTION_CAPABILITY,
        "target.revalidate_resolution",
    }
    if action != HandInspectionAction.OBSERVE:
        dependencies.add("zone.change.destination_replacement")
    if len(effects) == 2:
        if action not in {
            HandInspectionAction.DISCARD,
            HandInspectionAction.EXILE,
        }:
            return ()
        tail = effects[1]
        tail_dependencies = set(
            fixed_scry_node_capabilities(
                effects=(tail,),
                target_schema=None,
                mechanic_ids=mechanics,
            )
        ) | set(
            fixed_life_node_capabilities(
                effects=(tail,),
                target_schema=None,
                mechanic_ids=mechanics,
            )
        )
        if len(tail_dependencies) != 1:
            return ()
        dependencies.update(tail_dependencies)
    return tuple(sorted(dependencies))


__all__ = [
    "FIXED_HAND_INSPECTION_CAPABILITY",
    "FIXED_HAND_INSPECTION_MECHANIC",
    "fixed_hand_inspection_node_capabilities",
]
