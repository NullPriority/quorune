"""One turn-long targeting rule for the resolving controller's current creatures."""

from collections.abc import Mapping


TURN_CREATURE_TARGET_PROHIBITION_OPERATION = "grant_turn_creature_target_prohibition"
TURN_CREATURE_TARGET_PROHIBITION_CAPABILITY = "target.prohibition.current_controller_creatures_for_turn"
TURN_CREATURE_TARGET_PROHIBITION_MECHANIC = "turn-creature-opponent-target-prohibition"
TURN_CREATURE_TARGET_PROHIBITION_WINDOW = "turn_creature_opponent_target_prohibition"


def turn_creature_target_prohibition_instruction():
    return {
        "op": TURN_CREATURE_TARGET_PROHIBITION_OPERATION,
        "schema_version": 1,
        "player": "$controller",
    }


def uses_turn_creature_target_prohibition(effect):
    return isinstance(effect, Mapping) and effect.get("op") == TURN_CREATURE_TARGET_PROHIBITION_OPERATION


def validate_turn_creature_target_prohibition(effect, *, actor=None):
    instruction = turn_creature_target_prohibition_instruction()
    if actor is not None:
        if type(actor) is not str or not actor or actor.startswith("$"):
            raise ValueError("The turn-long targeting rule requires its resolving player")
        instruction["player"] = actor
    if (not isinstance(effect, Mapping) or type(effect.get("schema_version")) is not int
            or dict(effect) != instruction):
        raise ValueError("The turn-long targeting rule requires its closed controller grant")


def current_turn_creature_target_prohibition(stats, turn_sequence):
    if TURN_CREATURE_TARGET_PROHIBITION_WINDOW not in stats:
        return False
    granted_turn = stats[TURN_CREATURE_TARGET_PROHIBITION_WINDOW]
    if type(granted_turn) is not int or not 0 <= granted_turn <= turn_sequence:
        raise ValueError("The turn-long targeting rule has an invalid grant turn")
    return granted_turn == turn_sequence
