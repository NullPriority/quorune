"""One exact fixed-token, mill, and source-loyalty repeat construction."""

from __future__ import annotations

from ..creature_subtypes import canonical_creature_subtype
from ..replacement.immutable import FrozenMap
from ..rules.source_references import SourceReferenceSpec
from ..rules.token_mill_repeat import TOKEN_MILL_REPEAT_MECHANIC, TokenMillRepeatSpec
from .regex_working_set import fullmatch, IGNORECASE
from .token_templates import fixed_token_creation_effect_template


def fixed_token_mill_repeat_template(text: str, *, card_name: str):
    subject = SourceReferenceSpec(card_name).regex_pattern
    match = fullmatch(
        r"(?P<token>Create [^.!?]+), then mill a card\. "
        r"If an? (?P<subtype>[^.!?]+) card was milled this way, "
        rf"put a loyalty counter on (?:This planeswalker|{subject}) and repeat this process\.",
        " ".join(text.split()),
        IGNORECASE,
    )
    if match is None:
        return None
    subtype = canonical_creature_subtype(match["subtype"])
    token = fixed_token_creation_effect_template(match["token"] + ".")
    if subtype is None or token is None or token.template_id != "create-fixed-creature-token-v2":
        return None
    try:
        spec = TokenMillRepeatSpec(FrozenMap(token.effect), subtype)
    except ValueError:
        return None
    return "fixed-token-mill-subtype-repeat-v1", (token.effect, spec.to_effect()), None, (TOKEN_MILL_REPEAT_MECHANIC,)
