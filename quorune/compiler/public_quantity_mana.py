from __future__ import annotations

"""Closed mana amounts produced from existing public values.

The descriptor uses existing public quantity producers and closed color choices;
cost and gameplay admission remain separately capability gated.
"""

import re

from ..characteristic_fragments import (
    CharacteristicQuantityScope,
    CharacteristicQuantitySpec,
)
from ..abilities import _mana_spend_restriction
from ..rules.source_references import SourceReferenceSpec
from ..scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec
from .query_characteristic_templates import (
    query_characteristic_quantity,
    query_characteristic_value,
)
from .scalar_effect_amounts import _HISTORY


from ..public_quantity_mana_model import PublicQuantityManaOutput as PublicQuantityManaTemplate


def _amount(value: str, *, source_name: str, count: bool):
    normalized = " ".join(value.rstrip(".").split())
    quantity = (
        query_characteristic_quantity(normalized, source_name=source_name, definition_extensions=True)
        if count else query_characteristic_value(normalized, source_name=source_name)
    )
    if quantity is not None:
        return quantity
    counter = re.fullmatch(
        r"(?:the number of )?(?P<counter>[A-Za-z0-9+/-]+) counters? on "
        r"(?P<source>.+)", normalized, re.IGNORECASE,
    )
    if counter is not None and (
        re.fullmatch(r"this (?:creature|artifact|land|enchantment|permanent)", counter["source"], re.I)
        or SourceReferenceSpec(source_name).matches(counter["source"])
    ):
        return CharacteristicQuantitySpec(
            scope=CharacteristicQuantityScope.SOURCE_COUNTER,
            counter_name=counter["counter"],
        )
    scalar = re.fullmatch(r"(?P<source>.+?)['’]s (?P<field>power|toughness)", normalized, re.I)
    if scalar is not None and (
        re.fullmatch(r"this (?:creature|artifact|land|enchantment|permanent)", scalar["source"], re.I)
        or SourceReferenceSpec(source_name).matches(scalar["source"])
    ):
        return ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE, characteristic=scalar["field"].lower())
    history = _HISTORY.get(normalized.casefold())
    if history is not None:
        return ScalarEffectAmountSpec(ScalarAmountOrigin.HISTORY, history_fact=history)
    return None


def public_quantity_mana_template(text: str, *, source_name: str) -> PublicQuantityManaTemplate | None:
    """Consume one whole mana instruction, with no linked or conditional tail."""
    body = text.strip()
    restriction = None
    if ". Spend this mana only to " in body:
        body, _tail = body.split(". Spend this mana only to ", 1)
        restriction = _mana_spend_restriction(text)
        if restriction is None:
            return None
        body += "."
    fixed = re.fullmatch(r"Add \{(?P<color>[WUBRGC])\} for each (?P<amount>.+?)\.", body, re.I)
    equal = re.fullmatch(r"Add an amount of \{(?P<color>[WUBRGC])\} equal to (?P<amount>.+?)\.", body, re.I)
    variable = re.fullmatch(
        r"Add X mana (?P<selection>of any one color|in any combination of colors|"
        r"in any combination of (?P<colors>(?:\{[WUBRG]\})(?: and/or \{[WUBRG]\})?)), "
        r"where X is (?P<amount>.+?)\.", body, re.I,
    )
    match = fixed or equal or variable
    if match is None:
        return None
    amount = _amount(match["amount"], source_name=source_name, count=fixed is not None)
    if amount is None:
        return None
    if variable is None:
        selection = "fixed"
        colors = (match["color"].upper(),)
    else:
        selection = "choose_one" if variable["selection"].casefold() == "of any one color" else "combination"
        colors = tuple(sorted(set(re.findall(r"\{([WUBRG])\}", variable["colors"].upper())))) if variable["colors"] else tuple(sorted("WUBRG"))
    try:
        return PublicQuantityManaTemplate(selection, colors, amount, restriction)
    except (TypeError, ValueError):
        return None
