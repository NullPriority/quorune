"""Closed instructions and responses for private table-wide number choices."""

from __future__ import annotations

from collections.abc import Mapping

OPERATION = "secret_number_extremes"
REVEAL_EVENT = "semantic.secret_numbers.revealed"
MECHANIC = "secret-number-extremes"
CAPABILITY = "choice.secret_number.extremes"
DEPENDENCIES = (
    CAPABILITY, "damage.batch.fixed_set", "zone.discard.whole_hand", "zone.draw.library_to_hand",
)


def validate_extreme_number_instruction(effect: Mapping, *, materialized: bool = False) -> None:
    if (not isinstance(effect, Mapping)
            or set(effect) != {"op", "schema_version", "player", "count"}
            or effect["op"] != OPERATION or type(effect["schema_version"]) is not int
            or effect["schema_version"] != 1 or type(effect["count"]) is not int or effect["count"] < 1):
        raise ValueError("Secret number choices require the closed version-one damage and redraw instruction")
    player = effect["player"]
    if materialized:
        if type(player) is not str or not player or player.startswith("$"):
            raise ValueError("Secret number choices require the resolving controller")
    elif player != "$controller":
        raise ValueError("Secret number choices start with the resolving controller")


def secret_number_node_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or len(effects) != 1 or MECHANIC not in mechanic_ids:
        return ()
    try:
        validate_extreme_number_instruction(effects[0])
    except ValueError:
        return ()
    return DEPENDENCIES


def secret_number_payload_dependencies(effect):
    if effect.get("op") != OPERATION:
        return ()
    validate_extreme_number_instruction(effect)
    return DEPENDENCIES


def is_closed_secret_number_program(program):
    required = secret_number_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return (program.event == "resolve" and program.active_zone == "stack"
            and bool(required) and set(required).issubset(program.capability_dependencies))


def parse_secret_number_response(payload: Mapping) -> int:
    """Use decimal text so transport does not round values through binary floating point."""
    value = payload.get("number")
    if (set(payload) - {"number", "action", "choice_schema"} or payload.get("action", "choose") != "choose"
            or type(value) is not str or not value or not value.isascii() or not value.isdecimal()):
        raise ValueError("Choose a nonnegative whole number using decimal digits")
    return int(value.lstrip("0") or "0")


def secret_number_reveal_summary(choices) -> str | None:
    if (not isinstance(choices, (list, tuple)) or not choices or any(
        not isinstance(row, (list, tuple)) or len(row) != 2
        or type(row[0]) is not str or not row[0]
        or type(row[1]) is not str or not row[1].isascii() or not row[1].isdecimal()
        for row in choices
    )):
        return None
    description = ", ".join(f"{seat} chose {number}" for seat, number in choices)
    return f"All players reveal their chosen numbers. {description}."


def is_secret_number_decision(decision) -> bool:
    if decision.kind != "semantic.choice":
        return False
    effect = decision.continuation.get("effect")
    return isinstance(effect, Mapping) and effect.get("op") == OPERATION


def completion_actor_and_response(decision, controller):
    if is_secret_number_decision(decision):
        if set(decision.responses) != set(decision.actors):
            raise ValueError("Every seat must commit before secret numbers are revealed")
        return controller, {"number_commitments": tuple(
            (seat, parse_secret_number_response(decision.responses[seat])) for seat in decision.actors
        )}
    seat = decision.actors[0]
    return seat, decision.responses[seat]
