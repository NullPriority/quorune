"""Compose the printed controller life loss and an ordinary fixed token."""

from ..rules.fixed_effect_clause_shapes import FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC
from . import regex_working_set as re
from .fixed_controller_effect_sequences import fixed_controller_effect_clause
from .token_templates import fixed_token_creation_effect_template

_PATTERN = re.compile(r"(?P<life>you lose [1-9][0-9]* life) and create (?P<token>.+)", re.I)


def controller_life_loss_fixed_token_template(body):
    match = _PATTERN.fullmatch(body)
    if match is None:
        return None
    life = fixed_controller_effect_clause(match["life"] + ".")
    token = fixed_token_creation_effect_template("Create " + match["token"].rstrip(".") + ".")
    if life is None or token is None:
        return None
    effect, life_mechanics = life
    _, token_effects, targets, token_mechanics = token.compiled()
    if targets is not None or len(token_effects) != 1 or token_effects[0].get("op") != "create_token":
        return None
    return ("fixed-upkeep-life-loss-token-v1", (effect, token_effects[0]), None,
            tuple(dict.fromkeys((FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC, *life_mechanics, *token_mechanics))))
