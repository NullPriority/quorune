from __future__ import annotations

"""Capability shape for closed target-hand inspection programs."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..compiler.hand_inspection_templates import (
    FIXED_HAND_INSPECTION_CAPABILITY,
    FIXED_HAND_INSPECTION_MECHANIC,
)
from .hand_inspection import (
    FIXED_HAND_INSPECTION_OPERATION,
    HandCardPredicateSpec,
    HandInspectionAction,
    HandInspectionError,
    HandInspectionMode,
)
from .fixed_controller_effect_shapes import fixed_life_node_capabilities
from .node_capability_shapes import fixed_scry_node_capabilities


def fixed_hand_inspection_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize one inspected target hand with an optional fixed tail."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
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
        action == HandInspectionAction.OBSERVE
        and mode != HandInspectionMode.LOOK
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
