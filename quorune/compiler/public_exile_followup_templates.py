"""Bind a following instruction to a committed public graveyard exile."""
from __future__ import annotations

from . import regex_working_set as re

from ..object_predicate import ObjectQuerySpec
from ..public_zone_moves import PublicZoneMoveSetSpec
from ..rules.public_exile_result_model import PUBLIC_EXILE_RESULT_MECHANIC, PublicExileFollowup
from ..rules.opponent_exile_draw_results import OPPONENT_EXILE_DRAW_FIELD
from .per_player_target_sets import per_player_target_set_template
from .closed_effect_programs import CLOSED_EFFECT_PROGRAM_MECHANIC
from .targeted_graveyard_outcome_templates import targeted_graveyard_exile_outcome_template

_TERMINAL_COUNT = re.compile(
    r"(?P<effect>(?:Then )?(?:You gain (?:1|3) life|Put a \+1/\+1 counter on this creature|create a [^.]+? token)) "
    r"for each (?P<subject>creature card|card) exiled this way\.$", re.I,
)
_PLURAL_COUNTER = re.compile(
    r"Put a number of \+1/\+1 counters on this creature equal to the number of creature cards exiled this way\.$",
    re.I,
)
_PREFIXED_TOKEN = re.compile(
    r"For each (?P<subject>creature card|artifact or land card) exiled this way, "
    r"(?P<effect>create a [^.]+? token(?: with flying)?)\.$", re.I,
)


def _literal_followup(text: str):
    if match := _TERMINAL_COUNT.search(text):
        return match, match["effect"] + ".", match["subject"].casefold()
    if match := _PLURAL_COUNTER.search(text):
        return match, "Put a +1/+1 counter on this creature.", "creature card"
    if match := _PREFIXED_TOKEN.search(text):
        body = match["effect"]
        return match, body[0].upper() + body[1:] + ".", match["subject"].casefold()
    return None


_OPPONENT_EXILE_DRAW = re.compile(
    r"(?P<body>For each opponent, exile up to one target permanent that player controls[^.]*\.) "
    r"For each permanent exiled this way, its controller draws a card\.",
    re.IGNORECASE,
)


def _opponent_exile_draw_template(text: str):
    match = _OPPONENT_EXILE_DRAW.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    compiled = per_player_target_set_template(match["body"])
    if compiled is None:
        return None
    template, effects, schema, mechanics = compiled
    effect, = effects
    return (
        template + "-draw-result-v1",
        ({**effect, OPPONENT_EXILE_DRAW_FIELD: 1},),
        schema,
        (*mechanics, "cr-121-drawing-a-card"),
    )


def public_exile_followup_template(text: str, *, compile_literal):
    if opponent_draw := _opponent_exile_draw_template(text):
        return opponent_draw
    normalized = text.strip()
    if not normalized.casefold().startswith("exile "):
        return None
    targeted = targeted_graveyard_exile_outcome_template(normalized)
    if targeted is not None:
        return targeted
    literal = _literal_followup(normalized)
    if literal is None:
        return None
    match, replacement, subject = literal
    preceding = normalized[:match.start()].rstrip()
    if preceding.endswith(","):
        preceding = preceding[:-1] + "."
    _, effects, target_schema, mechanics = compile_literal(f"{preceding} {replacement}")
    if len(effects) != 2 or effects[0].get("op") != "move_public_zone_set":
        return None
    scope = ObjectQuerySpec(
        zones=("graveyard",), token=False,
        types_all=("creature",) if subject == "creature card" else (),
        types_any=("artifact", "land") if subject == "artifact or land card" else (),
    )
    try:
        followup = PublicExileFollowup(scope, effects[1])
        followup.validate_move(PublicZoneMoveSetSpec.from_dict(effects[0]["set"]))
    except (TypeError, ValueError, KeyError):
        return None
    markers = tuple(
        mechanic for mechanic in mechanics
        if mechanic not in {"fixed-effect-clause-sequence", CLOSED_EFFECT_PROGRAM_MECHANIC, "bound-effect-program"}
    )
    return (
        "fixed-graveyard-exile-result-v1",
        ({**effects[0], "followup": followup.to_dict()},),
        target_schema,
        (*markers, PUBLIC_EXILE_RESULT_MECHANIC),
    )
