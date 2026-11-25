"""Lower a targeted whole-hand discard with a fixed life alternative."""

from . import regex_working_set as re
from .whole_hand_discard_templates import whole_hand_discard_effect_template
from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC, FixedEffectPaymentSpec

_TARGET_LIFE = re.compile(
    r"^(?P<result>Target player discards their hand) unless they pay (?P<amount>[1-9][0-9]*) life\.$",
    re.IGNORECASE,
)


def targeted_life_decline_template(text):
    match = _TARGET_LIFE.fullmatch(text.strip())
    if match is None:
        return None
    try:
        cost = FixedEffectPaymentSpec(kind="life", amount=int(match["amount"]))
    except ValueError:
        return None
    _, fallback, target_schema, mechanics = whole_hand_discard_effect_template(match["result"] + ".")
    instruction = {
        "op": "offer_optional_mana_payment",
        "schema_version": 2,
        "player": "$target.0",
        "payment": cost.to_dict(),
        "effects": [],
        "declined_effects": list(fallback),
    }
    return (
        "targeted-whole-hand-discard-unless-life-v1", (instruction,), target_schema,
        (FIXED_EFFECT_PAYMENT_MECHANIC, *mechanics),
    )
