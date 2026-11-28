from __future__ import annotations

"""Typed Offering declarations and reductions under CR 118.7 and 702.48."""

from collections.abc import Mapping
from dataclasses import dataclass

from .creature_subtypes import canonical_creature_subtype
from .replacement.immutable import FrozenMap
from .util import parse_mana_symbols


OFFERING_CAPABILITY = "casting.additional_cost.offering"
OFFERING_HANDLER = "casting.cost.offering.v1"
OFFERING_FIELD = "offering"
MANA_KEYS = ("GENERIC", "W", "U", "B", "R", "G", "C")
_COLORED = frozenset("WUBRG")
_MANA_TYPES = _COLORED | {"C"}


@dataclass(frozen=True, slots=True)
class OfferingSpec:
    subtype: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.subtype) is not str
                or canonical_creature_subtype(self.subtype) != self.subtype):
            raise ValueError("Offering requires one canonical creature subtype")

    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version, "subtype": self.subtype}

    @classmethod
    def from_dict(cls, value: Mapping) -> OfferingSpec:
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "subtype"}:
            raise ValueError("Offering declaration requires exactly a version and a subtype")
        return cls(**dict(value))


def _symbol_reductions(symbol: str) -> tuple[tuple[str, int], ...]:
    if symbol in _MANA_TYPES:
        return ((symbol, 1),)
    if symbol == "X":
        return (("GENERIC", 0),)
    if symbol == "S":
        return (("GENERIC", 1),)
    if symbol.isascii() and symbol.isdecimal():
        return (("GENERIC", int(symbol)),)

    parts = symbol.split("/")
    if len(parts) in (2, 3) and parts[-1] == "P" and len(set(parts)) == len(parts):
        colors = parts[:-1]
        if set(colors) <= _COLORED:
            return tuple((color, 1) for color in colors)
    if len(parts) == len(set(parts)) == 2:
        if set(parts) <= _MANA_TYPES:
            return tuple((part, 1) for part in parts)
        if "2" in parts:
            color = next(part for part in parts if part != "2")
            if color in _MANA_TYPES:
                return ((color, 1), ("GENERIC", 2))
    raise ValueError("The sacrificed permanent has an unrepresented mana symbol")


def offering_reduction_vectors(mana_cost: str | None) -> tuple[FrozenMap, ...]:
    if mana_cost is None:
        mana_cost = ""
    if type(mana_cost) is not str:
        raise ValueError("Offering requires the current mana cost of the sacrificed permanent")
    symbols = parse_mana_symbols(mana_cost)
    if "".join("{" + symbol + "}" for symbol in symbols) != mana_cost.strip().upper():
        raise ValueError("The sacrificed permanent has a malformed mana cost")
    vectors = {
        FrozenMap(dict.fromkeys(MANA_KEYS, 0)),
    }
    for symbol in symbols:
        vectors = {
            FrozenMap({**dict(vector), key: vector[key] + amount})
            for vector in vectors for key, amount in _symbol_reductions(symbol)
        }
    return tuple(sorted(vectors, key=lambda vector: tuple(vector[key] for key in MANA_KEYS)))


def reduce_offering_total(requirements: Mapping, credit: Mapping) -> dict[str, int]:
    for value in (requirements, credit):
        if (not isinstance(value, Mapping) or set(value) != set(MANA_KEYS)
                or any(type(amount) is not int or amount < 0 for amount in value.values())):
            raise ValueError("Offering requires exact cost and reduction vectors")
    generic = credit["GENERIC"]
    reduced = {}
    for kind in MANA_KEYS[1:]:
        balance = requirements[kind] - credit[kind]
        reduced[kind] = max(0, balance)
        generic += max(0, -balance)
    return {"GENERIC": max(0, requirements["GENERIC"] - generic), **reduced}
