"""Lower a closed targetless result that replaces the controller's next draw."""

from . import regex_working_set as re
from ..next_draw_replacement_model import NEXT_DRAW_REPLACEMENT_MECHANIC, PUBLIC_PERMANENT_RETURN_RESULT, NextDrawResult
from ..replacement.immutable import FrozenMap
from ..rules.next_draw_replacement_shapes import require_scheduled_draw_result_ownership
from .fixed_numbers import fixed_number
from .token_templates import fixed_token_creation_effect_template


_PREFIX = re.compile(
    r"The next time you would draw a card this turn, (?P<result>.+) instead\.",
    re.IGNORECASE,
)
_DISCARD = re.compile(
    r"each opponent discards (?P<count>a|one|two|three|four|five|six|seven|eight|nine|ten|\d+) cards?",
    re.IGNORECASE,
)
_GAIN = re.compile(r"you gain (?P<amount>[1-9]\d*) life", re.IGNORECASE)
_PUBLIC_RETURN = re.compile(r"each player returns a permanent they control to its owner['’]s hand", re.IGNORECASE)


def next_draw_replacement_template(text: str) -> NextDrawResult | None:
    match = _PREFIX.fullmatch(" ".join(text.strip().split()))
    if match is None:
        return None
    result = match["result"]
    discard = _DISCARD.fullmatch(result)
    if discard is not None:
        count = 1 if discard["count"].casefold() == "a" else fixed_number(discard["count"])
        return NextDrawResult("discard_each_opponent", FrozenMap({"count": count})) if count and count > 0 else None
    gain = _GAIN.fullmatch(result)
    if gain is not None:
        return NextDrawResult("gain_life", FrozenMap({"amount": int(gain["amount"])}))
    if _PUBLIC_RETURN.fullmatch(result):
        return NextDrawResult(PUBLIC_PERMANENT_RETURN_RESULT, FrozenMap())
    if not result.casefold().startswith("create "):
        return None
    token = fixed_token_creation_effect_template(result + ".")
    if token is None or token.target_schema is not None:
        return None
    try:
        result = NextDrawResult("create_token", FrozenMap({
            "token": token.effect, "token_mechanics": token.mechanics,
        }))
        require_scheduled_draw_result_ownership(result)
        return result
    except ValueError:
        return None


def next_draw_replacement_effect_template(text):
    result = next_draw_replacement_template(text)
    if result is None:
        return None
    return "controller-next-draw-replacement-v1", (result.instruction(),), None, (NEXT_DRAW_REPLACEMENT_MECHANIC,)
