from __future__ import annotations

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from ..turn_skip_model import (
    SKIP_TAP_PLAYER, TARGET_PLAYER_UNTAP_SKIP_TAP_CAPABILITY, TARGET_PLAYER_UNTAP_SKIP_TAP_MECHANIC,
    TURN_SKIP_CAPABILITY, TURN_SKIP_MECHANIC, TURN_SKIP_OPERATION, TargetPlayerUntapSkipTapSpec,
    decode_turn_skip_instruction,
)


def _uses_target_player_skip_and_tap(effects, mechanics):
    return TARGET_PLAYER_UNTAP_SKIP_TAP_MECHANIC in mechanics or any(
        isinstance(effect, Mapping) and effect.get("op") == TURN_SKIP_OPERATION
        and effect.get("player") == SKIP_TAP_PLAYER for effect in effects
    )


def _target_player_skip_and_tap_capabilities(effects, target_schema, mechanics):
    from ..compiler.fixed_homogeneous_target_sets import FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC
    from .fixed_homogeneous_target_set_capability_shapes import fixed_homogeneous_target_set_node_capabilities

    if (len(effects) != 2 or not all(isinstance(effect, Mapping) for effect in effects)
            or not isinstance(target_schema, Mapping)):
        return ()
    try:
        spec = TargetPlayerUntapSkipTapSpec(effects[1].get("maximum_targets"))
    except ValueError:
        return ()
    expected = spec.effects()
    if (tuple(FrozenMap(effect) for effect in effects) != tuple(FrozenMap(effect) for effect in expected)
            or FrozenMap(target_schema) != FrozenMap(spec.target_schema())
            or type(target_schema["groups"][0]["count"]) is not int
            or type(target_schema["groups"][1]["up_to"]) is not int
            or type(effects[0]["spec"]["count"]) is not int):
        return ()
    fields = dict(spec.target_schema()["groups"][1])
    fields.pop("id")
    tap = {**effects[1], "cards": "$targets"}
    tap_capabilities = fixed_homogeneous_target_set_node_capabilities(
        effects=(tap,), target_schema=fields,
        mechanic_ids=(*mechanics, FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC, "tap-and-untap", "cr-115-targets"),
    )
    return (TARGET_PLAYER_UNTAP_SKIP_TAP_CAPABILITY, TURN_SKIP_CAPABILITY,
            "target.revalidate_resolution", *tap_capabilities) if tap_capabilities else ()


def scheduled_turn_skip_node_capabilities(*, effects, target_schema, mechanic_ids):
    if _uses_target_player_skip_and_tap(effects, mechanic_ids):
        return _target_player_skip_and_tap_capabilities(effects, target_schema, mechanic_ids)
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
    """Validate occurrence recipients and the dependent target groups."""
    if _uses_target_player_skip_and_tap(program.effects, program.coverage):
        dependencies = _target_player_skip_and_tap_capabilities(
            program.effects, program.target_schema, program.coverage,
        )
        if not dependencies:
            raise ValueError("Target player skip and tap require the original related target groups")
        return dependencies

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
