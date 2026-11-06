from __future__ import annotations

from collections.abc import Mapping

from ..turn_skip_model import (
    TURN_SKIP_CAPABILITY, TURN_SKIP_MECHANIC, TURN_SKIP_OPERATION, decode_turn_skip_instruction,
)


def scheduled_turn_skip_node_capabilities(*, effects, target_schema, mechanic_ids):
    if TURN_SKIP_MECHANIC not in mechanic_ids or len(effects) != 1:
        return ()
    effect = effects[0]
    if not isinstance(effect, Mapping):
        return ()
    try:
        decode_turn_skip_instruction(effect)
    except (TypeError, ValueError):
        return ()
    if effect.get("player") == "$target.0":
        if not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int:
            return ()
        if target_schema in (
            {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "any"},
            {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "opponent"},
        ):
            return TURN_SKIP_CAPABILITY, "target.revalidate_resolution"
        return ()
    if target_schema is None and (
        effect.get("player") in ("$controller", "$context.target") or effect.get("players") == "opponents"
    ):
        return (TURN_SKIP_CAPABILITY,)
    return ()


def is_closed_scheduled_turn_skip_program(program):
    required = scheduled_turn_skip_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage
    )
    if not required or not set(required).issubset(program.capability_dependencies):
        return False
    try:
        scheduled_turn_skip_event_dependencies(program)
    except ValueError:
        return False
    return True


def scheduled_turn_skip_event_dependencies(program):
    """Validate occurrence recipients even when the instruction is nested."""

    def uses_damage_player(value):
        if isinstance(value, Mapping):
            if value.get("op") == TURN_SKIP_OPERATION and value.get("player") == "$context.target":
                return True
            return any(uses_damage_player(child) for child in value.values())
        return isinstance(value, (list, tuple)) and any(uses_damage_player(child) for child in value)

    if not uses_damage_player(program.effects):
        return ()
    from .event_player_result_shapes import _event_player_reference_is_closed

    if not _event_player_reference_is_closed(program, "$context.target"):
        raise ValueError("Scheduled skip requires a sealed damage recipient player")
    return (TURN_SKIP_CAPABILITY,)
