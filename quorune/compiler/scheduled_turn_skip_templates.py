from __future__ import annotations

"""Fixed scheduled skips with explicit recipient and occurrence scope."""

import re

from ..turn_skip_model import (
    MAX_PRINTED_SKIP_COUNT, TURN_SKIP_MECHANIC, TURN_SKIP_OPERATION,
    TurnBoundary, TurnSkipScope, TurnSkipSpec,
)
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


_SUBJECT = re.compile(
    r"(?P<subject>You skip|Skip|Target player skips|Target opponent skips"
    r"|Each opponent skips) (?P<body>.+?)\.?",
    re.IGNORECASE,
)
_TURN_COUNT = re.compile(rf"next (?:(?P<count>{FIXED_COUNT_PATTERN}) )?turn(?P<plural>s)?", re.I)
_STEP = re.compile(r"next (?P<step>untap|upkeep|draw) step", re.I)


def _skip_spec(body: str) -> TurnSkipSpec | None:
    if match := _TURN_COUNT.fullmatch(body):
        count = fixed_number(match["count"]) if match["count"] else 1
        if not 1 <= count <= MAX_PRINTED_SKIP_COUNT or bool(match["plural"]) != (count != 1):
            return None
        return TurnSkipSpec(TurnBoundary.TURN, count=count)
    if match := _STEP.fullmatch(body):
        return TurnSkipSpec(TurnBoundary(match["step"].casefold()))
    scopes = {
        "next combat phase": TurnSkipScope.NEXT,
        "next combat phase this turn": TurnSkipScope.THIS_TURN,
    }
    scope = scopes.get(body.casefold())
    return TurnSkipSpec(TurnBoundary.COMBAT, scope) if scope is not None else None


def scheduled_turn_skip_effect_template(text: str):
    match = _SUBJECT.fullmatch(" ".join(text.strip().split()))
    if match is None:
        return None
    body, subject = match["body"].casefold(), match["subject"].casefold()
    possessive = "your" if subject in {"you skip", "skip"} else "their"
    if body == f"all combat phases of {possessive} next turn":
        spec = TurnSkipSpec(TurnBoundary.COMBAT, TurnSkipScope.ALL_OF_NEXT_TURN)
    elif body.startswith(f"{possessive} "):
        spec = _skip_spec(body.removeprefix(f"{possessive} "))
    else:
        return None
    if spec is None:
        return None
    targeted = subject.startswith("target ")
    effect = {"op": TURN_SKIP_OPERATION, "spec": spec.to_dict()}
    schema = None
    if targeted:
        schema = {"zones": ["player"], "categories": ["player"], "count": 1,
                  "player_relation": "opponent" if subject == "target opponent skips" else "any"}
        effect["player"] = "$target.0"
    elif subject == "each opponent skips":
        effect["players"] = "opponents"
    else:
        effect["player"] = "$controller"
    mechanics = (TURN_SKIP_MECHANIC, *(("cr-115-targets",) if targeted else ()))
    return "fixed-scheduled-turn-skip-v1", (effect,), schema, mechanics


def damage_player_turn_skip_result(binding, body: str):
    match = re.fullmatch(r"(?:That player skips|They skip) (?P<body>their .+)", body, re.I)
    if match is None:
        return None
    from .event_player_results import _event_player

    if _event_player(binding, "that player") != "$context.target":
        return None
    compiled = scheduled_turn_skip_effect_template(f"Target player skips {match['body']}")
    if compiled is None:
        return None
    effect, = compiled[1]
    return ("fixed-damage-player-turn-skip-v1", ({**effect, "player": "$context.target"},),
            None, (TURN_SKIP_MECHANIC,)), False
