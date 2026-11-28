from __future__ import annotations

from . import regex_working_set as re
from ..creature_power_damage_model import CREATURE_POWER_DAMAGE_MECHANIC
from ..rules.created_token_fights import created_token_fight_effect
from .token_templates import fixed_token_creation_effect_template


_INSTRUCTION = re.compile(
    r"For each creature your opponents control, create (?:a|an) (?P<definition>.+?) creature token\. "
    r"Each of those tokens fights a different one of those creatures\.",
    re.IGNORECASE,
)


def created_token_fight_template(text):
    match = _INSTRUCTION.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    token = fixed_token_creation_effect_template(f"Create a {match['definition']} creature token.")
    if token is None or token.effect.get("op") != "create_token" or token.effect.get("quantity") != 1:
        return None
    return (
        "fixed-opponent-creature-token-fights-v1", (created_token_fight_effect(token.effect),), None,
        (*token.mechanics, CREATURE_POWER_DAMAGE_MECHANIC, "fight", "cr-120-damage"),
    )
