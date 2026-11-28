"""Bind optional attached-creature payment to a committed token-copy result."""

from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC
from ..rules.attached_copy_payment import TEMPLATE, attached_copy_payment, attached_copy_payment_parts
from ..rules.source_references import SourceReferenceSpec
from ..util import mana_cost_to_vector
from . import regex_working_set as re
from .token_templates import fixed_token_creation_effect_template

_ATTACHED_COPY = re.compile(
    r"You may pay (?P<cost>(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+) "
    r"if (?P<source>.+?) is attached to a creature you control\. "
    r"If you do, create a token that['’]s a copy of that creature\. "
    r"If you didn['’]t create a token this way, (?P<fallback>create .+? creature token\.)",
    re.I,
)


def attached_copy_payment_result(binding, body, *, card_name):
    if (binding.event.value, binding.variant, binding.active_zone) != ("land.enter", "controlled_land", "battlefield"):
        return None
    match = _ATTACHED_COPY.fullmatch(body)
    if match is None or (match["source"].casefold() != "this permanent"
                         and not SourceReferenceSpec(card_name).matches(match["source"])):
        return None
    token = fixed_token_creation_effect_template(match["fallback"])
    if token is None or token.target_schema is not None:
        return None
    vector, complex_symbols = mana_cost_to_vector(match["cost"])
    if complex_symbols or not any(vector.values()):
        return None
    try:
        effect = attached_copy_payment(vector, token.effect)
        attached_copy_payment_parts(effect)
    except ValueError:
        return None
    return (TEMPLATE, (effect,), None, (FIXED_EFFECT_PAYMENT_MECHANIC, *token.mechanics)), True
