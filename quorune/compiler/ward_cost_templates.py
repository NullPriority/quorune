from __future__ import annotations

"""Closed Oracle grammar for fixed public Ward payments."""

from . import regex_working_set as re

from ..trigger_participation import WardSpec
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from .ward_sacrifice_templates import ward_sacrifice_scope


_FIXED_GENERIC_WARD = re.compile(
    r"Ward\s+\{(?P<generic>\d+)\}\.?",
    re.IGNORECASE,
)
_FIXED_LIFE_WARD = re.compile(
    rf"Ward[—-]Pay (?P<amount>{FIXED_COUNT_PATTERN}|\d+) life\.?",
    re.IGNORECASE,
)
_DISCARD_ONE_WARD = re.compile(
    r"Ward[—-]Discard a card\.?",
    re.IGNORECASE,
)


_FIXED_BLIGHT_WARD = re.compile(r"Ward[—-]Blight (?P<amount>[1-9]\d*)\.?", re.IGNORECASE)


def fixed_ward_spec(text: str) -> WardSpec | None:
    """Parse one represented fixed Ward cost."""

    material = text.strip()
    generic = _FIXED_GENERIC_WARD.fullmatch(material)
    if generic is not None:
        return WardSpec(generic_cost=int(generic.group("generic")))
    life = _FIXED_LIFE_WARD.fullmatch(material)
    if life is not None:
        amount = fixed_number(life.group("amount"))
        return WardSpec(life_payment=amount) if amount > 0 else None
    if _DISCARD_ONE_WARD.fullmatch(material) is not None:
        return WardSpec(discard_cards=1)
    blight = _FIXED_BLIGHT_WARD.fullmatch(material)
    if blight is not None:
        return WardSpec(blight_amount=int(blight["amount"]))
    scope = ward_sacrifice_scope(material)
    return WardSpec(sacrifice_scope=scope) if scope is not None else None


def quoted_fixed_life_ward_spec(text: str) -> WardSpec | None:
    """Parse the quoted keyword as an ability instance, with a closed life cost."""
    match = re.fullmatch(r'"(?P<ward>Ward[^"]+\.)"', text.strip(), re.I)
    if match is None:
        return None
    spec = fixed_ward_spec(match["ward"])
    return spec if spec is not None and spec.life_payment is not None else None


__all__ = ["fixed_ward_spec", "quoted_fixed_life_ward_spec"]


def quoted_other_creature_life_ward(subject: str, text: str):
    """Lower the fixed-life Ward fragment granted to other controlled creatures."""
    if subject.casefold() != "other creatures you control":
        return None
    quoted = re.fullmatch(r'"(Ward[—-]Pay [^"]+ life\.)"', text, flags=re.IGNORECASE)
    ward = fixed_ward_spec(quoted[1]) if quoted is not None else None
    if ward is None or ward.life_payment is None:
        return None
    from ..ability_fragments import ability_fragment_to_dict

    granted = ((f"Ward—Pay {ward.life_payment} life",), (ability_fragment_to_dict(ward),))
    return granted, ward.capability_id


__all__ = ["fixed_ward_spec", "quoted_other_creature_life_ward"]
