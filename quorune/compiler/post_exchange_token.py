from __future__ import annotations

"""Lower a complete two-target life exchange followed by its sized artifact token."""

from ..rules.player_life_exchange import PlayerLifeExchangeSpec
from ..rules.post_exchange_token import post_exchange_token_instruction
from . import regex_working_set as re


def post_exchange_token_spell_template(text):
    if not re.fullmatch(
        r"Two target players exchange life totals\. You create an X/X colorless Horror artifact creature token, "
        r"where X is the difference between those players['’] life totals\.",
        " ".join(text.strip().split()), re.IGNORECASE,
    ):
        return None
    spec = PlayerLifeExchangeSpec("two_targets")
    return (
        "paired-life-exchange-then-token-v1", (spec.to_instruction(), post_exchange_token_instruction()),
        spec.to_target_schema(), ("cr-119-life", "cr-115-targets", "cr-111-tokens"),
    )
