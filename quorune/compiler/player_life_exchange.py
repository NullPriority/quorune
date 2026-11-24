from __future__ import annotations

"""Closed result grammar for exchanging the life totals of two players."""

from ..rules.player_life_exchange import PlayerLifeExchangeSpec
from . import regex_working_set as re


def player_life_exchange_template(text):
    body = " ".join(text.strip().split())
    if re.fullmatch(r"Two target players exchange life totals\.", body, re.I):
        scope = "two_targets"
    elif re.fullmatch(r"Exchange life totals with target opponent\.", body, re.I):
        scope = "controller_and_opponent"
    else:
        return None
    result = PlayerLifeExchangeSpec(scope)
    return (
        "two-player-life-exchange-v1", (result.to_instruction(),), result.to_target_schema(),
        ("cr-119-life", "cr-115-targets"),
    )
