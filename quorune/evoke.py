from __future__ import annotations

"""Typed fixed-mana Evoke cost and shared sacrifice-on-entry marker."""

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping

from .replacement.immutable import FrozenMap, thaw_value
from .util import parse_mana_symbols, stable_json


EVOKE_CAPABILITY_ID = "casting.evoke.fixed_mana"
EVOKE_HANDLER_ID = "casting.evoke.fixed-mana.v1"
EVOKE_MECHANIC_ID = "evoke"
EVOKE_RUNTIME_EVENT = "cast.cost"
EVOKE_PAYMENT_FIELD = "evoke_payment"
_ABILITY_ID = re.compile(r"^ab[1-9][0-9]*$")
_FIXED_SYMBOL = r"(?:0|[1-9]\d*|[WUBRGC]|[WUBRGC]/[WUBRGC])"
_FIXED_COST = rf"(?:\{{{_FIXED_SYMBOL}\}})+"
_EVOKE = re.compile(
    rf"^Evoke (?P<cost>{_FIXED_COST})(?:\s+\(.*\))?\.?$",
    re.IGNORECASE,
)
_MANA_FIELDS = ("GENERIC", "W", "U", "B", "R", "G", "C")


class EvokeError(ValueError):
    """A fixed-mana Evoke descriptor or marker is malformed."""


def _empty_vector() -> dict[str, int]:
    return {field: 0 for field in _MANA_FIELDS}


def fixed_mana_variants(cost_text: str) -> tuple[FrozenMap, ...]:
    if type(cost_text) is not str or re.fullmatch(_FIXED_COST, cost_text) is None:
        raise EvokeError("Evoke cost must use fixed ordinary or colored-hybrid mana")
    variants = [_empty_vector()]
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
            raise EvokeError("Evoke supports only fixed colored-hybrid symbols")
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


@dataclass(frozen=True, slots=True)
class FixedManaEvokeSpec:
    ability_id: str
    line_index: int
    oracle_line: str
    cost_text: str
    mana_variants: tuple[FrozenMap, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise EvokeError("Unsupported fixed-mana Evoke schema version")
        if type(self.ability_id) is not str or _ABILITY_ID.fullmatch(self.ability_id) is None:
            raise EvokeError("Evoke ability ID must be abN")
        if type(self.line_index) is not int or self.line_index < 0:
            raise EvokeError("Evoke line index must be nonnegative")
        if self.ability_id != f"ab{self.line_index + 1}":
            raise EvokeError("Evoke ability ID does not match its source line")
        if type(self.oracle_line) is not str or _EVOKE.fullmatch(self.oracle_line.strip()) is None:
            raise EvokeError("Evoke Oracle line is outside the closed grammar")
        match = _EVOKE.fullmatch(self.oracle_line.strip())
        assert match is not None
        if type(self.cost_text) is not str or match.group("cost").upper() != self.cost_text:
            raise EvokeError("Evoke cost does not match its Oracle line")
        expected = fixed_mana_variants(self.cost_text)
        variants = tuple(
            value if isinstance(value, FrozenMap) else FrozenMap(value)
            for value in self.mana_variants
        )
        object.__setattr__(self, "mana_variants", variants)
        if variants != expected:
            raise EvokeError("Evoke mana variants do not match its cost")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(stable_json(self.to_dict()).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ability_id": self.ability_id,
            "line_index": self.line_index,
            "oracle_line": self.oracle_line,
            "cost_text": self.cost_text,
            "mana_variants": [thaw_value(value) for value in self.mana_variants],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixedManaEvokeSpec":
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "ability_id",
            "line_index",
            "oracle_line",
            "cost_text",
            "mana_variants",
        }:
            raise EvokeError("Fixed-mana Evoke descriptors have a closed schema")
        variants = value["mana_variants"]
        if not isinstance(variants, list) or any(
            not isinstance(item, Mapping) for item in variants
        ):
            raise EvokeError("Evoke mana variants must be an array of objects")
        return cls(
            ability_id=value["ability_id"],
            line_index=value["line_index"],
            oracle_line=value["oracle_line"],
            cost_text=value["cost_text"],
            mana_variants=tuple(FrozenMap(item) for item in variants),
            schema_version=value["schema_version"],
        )

    def cast_cost_options(self) -> tuple[dict[str, Any], ...]:
        multiple = len(self.mana_variants) > 1
        return tuple(
            {
                "id": f"evoke-hybrid-{index}" if multiple else "evoke",
                "kind": "alternate",
                "label": f"Evoke {self.cost_text}",
                "requirements": thaw_value(requirements),
                "evoke_fingerprint": self.fingerprint,
                EVOKE_PAYMENT_FIELD: evoke_payment_marker(),
            }
            for index, requirements in enumerate(self.mana_variants, start=1)
        )


def compile_fixed_mana_evoke(
    *, material_line: str, oracle_line: str, line_index: int,
) -> FixedManaEvokeSpec | None:
    match = _EVOKE.fullmatch(material_line.strip())
    if match is None:
        return None
    cost_text = match.group("cost").upper()
    try:
        variants = fixed_mana_variants(cost_text)
    except EvokeError:
        return None
    return FixedManaEvokeSpec(
        ability_id=f"ab{line_index + 1}",
        line_index=line_index,
        oracle_line=oracle_line,
        cost_text=cost_text,
        mana_variants=variants,
    )


def evoke_payment_marker() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": EVOKE_MECHANIC_ID,
        "sacrifice_on_entry": True,
    }


def validate_evoke_payment_marker(value: Any) -> bool:
    return bool(
        isinstance(value, Mapping)
        and set(value) == {"schema_version", "kind", "sacrifice_on_entry"}
        and type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["kind"] == EVOKE_MECHANIC_ID
        and value["sacrifice_on_entry"] is True
    )


__all__ = [
    "EVOKE_CAPABILITY_ID",
    "EVOKE_HANDLER_ID",
    "EVOKE_MECHANIC_ID",
    "EVOKE_PAYMENT_FIELD",
    "EVOKE_RUNTIME_EVENT",
    "EvokeError",
    "FixedManaEvokeSpec",
    "compile_fixed_mana_evoke",
    "evoke_payment_marker",
    "fixed_mana_variants",
    "validate_evoke_payment_marker",
]
