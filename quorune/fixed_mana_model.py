"""Pure fixed ordinary/hybrid mana expansion shared by casting mechanics."""
from .replacement.immutable import FrozenMap,thaw_value
from .util import parse_mana_symbols
import re

_FIXED_SYMBOL=r"(?:0|[1-9]\d*|[WUBRGC]|[WUBRGC]/[WUBRGC])"
FIXED_MANA_COST_PATTERN=rf"(?:\{{{_FIXED_SYMBOL}\}})+"
_MANA_FIELDS=("GENERIC","W","U","B","R","G","C")

class FixedManaCostError(ValueError):
    """A fixed mana expression is malformed or outside the closed grammar."""

def _empty_mana_vector():return {field:0 for field in _MANA_FIELDS}

def fixed_mana_variants(cost_text: str) -> tuple[FrozenMap, ...]:
    if type(cost_text) is not str or re.fullmatch(FIXED_MANA_COST_PATTERN, cost_text) is None:
        raise FixedManaCostError("Fixed mana cost must use fixed ordinary or colored-hybrid mana")
    variants = [_empty_mana_vector()]
    for raw_symbol in parse_mana_symbols(cost_text):
        symbol = raw_symbol.upper()
        if symbol.isdigit():
            for variant in variants:
                variant["GENERIC"] += int(symbol)
            continue
        if symbol in "WUBRGC" and len(symbol) == 1:
            for variant in variants:
                variant[symbol] += 1
            continue
        hybrid = symbol.split("/")
        if len(hybrid) != 2 or any(
            value not in "WUBRGC" or len(value) != 1 for value in hybrid
        ):
            raise FixedManaCostError("Fixed mana supports only fixed colored-hybrid symbols")
        variants = [
            FrozenMap({**variant, color: variant[color] + 1})
            for variant in variants
            for color in hybrid
        ]
        variants = [dict(thaw_value(variant)) for variant in variants]
    unique: list[FrozenMap] = []
    seen: set[tuple[int, ...]] = set()
    for variant in variants:
        identity = tuple(variant[field] for field in _MANA_FIELDS)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(FrozenMap(variant))
    return tuple(unique)
