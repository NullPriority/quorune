from __future__ import annotations

"""Capability shape for one fixed next-turn upkeep draw payload."""

from typing import Iterable, Mapping, Sequence

from ..compiler.delayed_draw_templates import (
    FIXED_NEXT_TURN_DRAW_CAPABILITY,
    FIXED_NEXT_TURN_DRAW_MECHANIC,
    fixed_next_turn_upkeep_draw_effect,
)
from ..turn_creature_cast_draw_model import (
    TURN_CREATURE_CAST_DRAW_FIELD,
    validate_turn_creature_cast_draw_effect,
)


def fixed_next_turn_draw_node_capabilities(
    *,
    effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return trust dependencies only for the canonical delayed draw shape."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        FIXED_NEXT_TURN_DRAW_MECHANIC not in mechanics
        or target_schema is not None
        or tuple(effects) != (fixed_next_turn_upkeep_draw_effect(),)
    ):
        return ()
    return (
        FIXED_NEXT_TURN_DRAW_CAPABILITY,
        "trigger.placement.apnap",
        "zone.draw.library_to_hand",
    )


def turn_creature_cast_draw_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (target_schema is not None or len(effects) != 1
            or not {"cr-121-drawing-a-card", "cr-603-handling-triggered-abilities"} <= set(mechanic_ids)):
        return ()
    try:
        validate_turn_creature_cast_draw_effect(effects[0])
    except ValueError:
        return ()
    return ("trigger.event.normalized_spell_cast", "trigger.placement.apnap", "zone.draw.library_to_hand")


def turn_creature_cast_draw_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(TURN_CREATURE_CAST_DRAW_FIELD in effect for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = turn_creature_cast_draw_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (
        not required
        or (program.event, program.active_zone) != ("resolve", "stack")
        or program.event_condition is not None
        or program.handlers
        or program.trigger_limit is not None
    ):
        raise ValueError("Same-turn creature-cast draw requires its complete resolving spell")
    return required


def is_closed_turn_creature_cast_draw_program(program):
    try:
        required = turn_creature_cast_draw_runtime_dependencies(program)
    except ValueError:
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def delayed_draw_node_capabilities(**values):
    return fixed_next_turn_draw_node_capabilities(**values) or turn_creature_cast_draw_node_capabilities(**values)


__all__ = [
    "delayed_draw_node_capabilities",
    "fixed_next_turn_draw_node_capabilities",
    "turn_creature_cast_draw_node_capabilities",
    "turn_creature_cast_draw_runtime_dependencies",
    "is_closed_turn_creature_cast_draw_program",
]
