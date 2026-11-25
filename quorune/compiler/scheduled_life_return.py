"""Lower two closed upkeep life-price alternatives through the scheduled participant."""

from . import regex_working_set as re
from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC, FixedEffectPaymentSpec
from ..rules.scheduled_life_return import (
    SCHEDULED_LIFE_RETURN_FIELD, UPKEEP_PARTICIPANT, controlled_return_for_payer,
)
from ..rules.scheduled_player_shapes import SCHEDULED_PLAYER_MECHANIC

_RETURN = r"a permanent they control to its owner['’]s hand"
_UNLESS = re.compile(r"That player returns " + _RETURN + r" unless they pay (?P<cost>[1-9][0-9]*) life\.", re.I)
_IF_DECLINED = re.compile(
    r"That player may pay (?P<cost>[1-9][0-9]*) life\. If they don['’]t, they return " + _RETURN + r"\.", re.I,
)


def scheduled_life_return_result(binding, body):
    if binding.variant != "each_player_upkeep" or body != binding.body:
        return None
    match = _UNLESS.fullmatch(body) or _IF_DECLINED.fullmatch(body)
    if match is None:
        return None
    try:
        price = FixedEffectPaymentSpec(kind="life", amount=int(match["cost"]))
    except ValueError:
        return None
    effect = {
        "op": "offer_optional_mana_payment", "schema_version": 2,
        SCHEDULED_LIFE_RETURN_FIELD: 1, "player": UPKEEP_PARTICIPANT,
        "payment": price.to_dict(), "effects": [],
        "declined_effects": [controlled_return_for_payer("$controller", UPKEEP_PARTICIPANT)],
    }
    return (
        ("scheduled-player-life-or-controlled-return-v1", (effect,), None,
         (FIXED_EFFECT_PAYMENT_MECHANIC, SCHEDULED_PLAYER_MECHANIC)),
        True,
    )
