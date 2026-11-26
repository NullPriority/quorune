"""Commit the targeting rule through the original resolving ability."""

from ..continuous_effect_state import resolution_effect_source
from ..errors import GameRuleError
from ..turn_creature_target_prohibition_model import (
    TURN_CREATURE_TARGET_PROHIBITION_WINDOW, validate_turn_creature_target_prohibition,
)


def apply_turn_creature_target_prohibition(host, effect, *, actor, operation, reason):
    instruction = {key: value for key, value in effect.items() if key != "_runtime_source"}
    try:
        validate_turn_creature_target_prohibition(instruction, actor=actor)
        source = resolution_effect_source(host, effect)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    item = next((item for item in host.state.stack if item.ref == source.stack_ref), None)
    if (item is None or item.kind != "triggered_ability" or item.controller != actor
            or item.context.get("currently_resolving") is not True):
        raise GameRuleError("The turn-long targeting rule requires its original resolving ability")
    host._require_seat(actor, in_game=True)
    host.state.players[actor].stats[TURN_CREATURE_TARGET_PROHIBITION_WINDOW] = host.state.turn_sequence
    host._log(
        actor, "effect.turn_creature_target_prohibition",
        f"{actor}'s creatures can't be targeted by opponents this turn.",
        {"player": actor, "reason": reason}, importance=2, changed_players=[actor],
    )
    return True
