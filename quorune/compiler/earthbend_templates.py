from __future__ import annotations

"""Closed Earthbend instructions over the shared land-target and quantity grammar."""

import re

from ..earthbend_model import EARTHBEND_MECHANIC, EARTHBEND_OPERATION
from ..query_effect_amount_model import PublicQueryAmountSpec
from .direct_target import direct_permanent_target_spec
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from .live_battlefield_quantities import resolution_quantity


_FIXED = re.compile(r"(?:you )?earthbend (?P<amount>0|[1-9][0-9]*)\.?", re.I)
_QUERY = re.compile(
    r"(?:you )?earthbend X, where X is (?P<twice>twice )?the number of (?P<subject>.+?)\.?",
    re.I,
)
_REPEATED = re.compile(
    rf"(?:you )?earthbend (?P<first>0|[1-9][0-9]*), then earthbend (?P<second>0|[1-9][0-9]*)"
    rf"(?:\. You gain (?P<life>{FIXED_COUNT_PATTERN}) life)?\.?",
    re.I,
)
_LINKED_UNTAP = re.compile(r"(?P<action>.+), then untap that land\.?", re.I)
EARTHBEND_GROUP_TARGETS = ("$target_group.target_0.0", "$target_group.target_1.0")


def earthbend_target_schema() -> dict:
    return direct_permanent_target_spec("target land you control").to_target_schema()


def repeated_earthbend_target_schema() -> dict:
    return {"groups": [{"id": f"target_{index}", **earthbend_target_schema()} for index in range(2)]}


def _repeated_earthbend(match):
    effects = tuple(
        {"op": EARTHBEND_OPERATION, "target": target, "amount": int(match[role])}
        for target, role in zip(EARTHBEND_GROUP_TARGETS, ("first", "second"), strict=True)
    )
    mechanics = (EARTHBEND_MECHANIC, "cr-115-targets")
    if match["life"] is not None:
        life = fixed_number(match["life"])
        if type(life) is not int or life < 1:
            return None
        effects += ({"op": "life", "player": "$controller", "delta": life},)
        mechanics += ("cr-119-life",)
    return "repeated-earthbend-v1", effects, repeated_earthbend_target_schema(), mechanics


def earthbend_effect_template(text: str, *, source_name: str):
    normalized = " ".join(text.split())
    if repeated := _REPEATED.fullmatch(normalized):
        return _repeated_earthbend(repeated)
    untap = _LINKED_UNTAP.fullmatch(normalized)
    if untap is not None:
        normalized = untap["action"]
    if fixed := _FIXED.fullmatch(normalized):
        amount = int(fixed["amount"])
    elif query := _QUERY.fullmatch(normalized):
        quantity = resolution_quantity(query["subject"], source_name=source_name)
        if quantity is None:
            return None
        try:
            amount = PublicQueryAmountSpec(quantity, coefficient=2 if query["twice"] else 1).to_dict()
        except ValueError:
            return None
    else:
        return None
    effects = ({"op": EARTHBEND_OPERATION, "target": "$target.0", "amount": amount},)
    mechanics = (EARTHBEND_MECHANIC, "cr-115-targets")
    if untap is not None:
        effects += ({"op": "untap", "card": "$target.0"},)
        mechanics += ("tap-and-untap",)
    return (
        "targeted-earthbend-v1",
        effects,
        earthbend_target_schema(),
        mechanics,
    )
