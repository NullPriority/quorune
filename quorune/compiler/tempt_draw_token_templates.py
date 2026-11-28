"""Recognize the complete Tempting Offer that repeats one draw and token pair."""

from . import regex_working_set as re
from .token_templates import fixed_token_creation_effect_template
from ..rules.tempt_draw_token import MECHANIC, all_effects, offer_node_capabilities


_PATTERN = re.compile(
    r"(?:Tempting Offer \N{EM DASH} )?Draw a card and (?P<token>create a .+?)\. "
    r"Then each opponent may draw a card and (?P=token)\. "
    r"For each opponent who does, you draw a card and you (?P=token)\.", re.I,
)


def tempt_draw_token_template(text):
    match = _PATTERN.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    token = fixed_token_creation_effect_template(match["token"] + ".")
    if token is None:
        return None
    _, children, schema, _mechanics = token.compiled()
    if schema is not None or len(children) != 1:
        return None
    effects = all_effects(children[0])
    if not offer_node_capabilities(effects=effects, target_schema=None, mechanic_ids=(MECHANIC,)):
        return None
    return "tempt-draw-and-token-v1", effects, None, (MECHANIC,)
