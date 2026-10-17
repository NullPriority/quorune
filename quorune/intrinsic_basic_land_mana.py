from __future__ import annotations

"""Typed CR 305.6 intrinsic mana declarations.

The rules derive these abilities from an object's current land card type and
basic land subtypes. Oracle reminder text is evidence for the printed card,
not an executable ability source.
"""

from dataclasses import dataclass
import re
from typing import Any, ClassVar, Mapping

from .characteristic_evaluation import type_parts
from .basic_land_types import BASIC_LAND_MANA


INTRINSIC_BASIC_LAND_MANA_CAPABILITY = "mana.intrinsic.basic_land_type"
_REMINDER_SEPARATOR = re.compile(r"\s+[—-]\s+")
_TYPE_WORD = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)*")


class IntrinsicBasicLandManaError(ValueError):
    """An intrinsic basic-land mana declaration is malformed."""


@dataclass(frozen=True, slots=True)
class IntrinsicBasicLandManaSpec:
    """One immutable basic-land subtype to mana-ability declaration."""

    basic_land_type: str
    mana_symbol: str

    SCHEMA_VERSION: ClassVar[int] = 1
    RULE_ID: ClassVar[str] = "305.6"
    CAPABILITY_ID: ClassVar[str] = INTRINSIC_BASIC_LAND_MANA_CAPABILITY

    def __post_init__(self) -> None:
        subtype = self.basic_land_type.casefold()
        expected = BASIC_LAND_MANA.get(subtype)
        if self.basic_land_type != subtype or expected is None:
            raise IntrinsicBasicLandManaError(
                "basic_land_type must be a canonical basic land subtype"
            )
        if self.mana_symbol != expected:
            raise IntrinsicBasicLandManaError(
                "mana_symbol must match the basic land subtype"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "intrinsic_basic_land_mana",
            "schema_version": self.SCHEMA_VERSION,
            "basic_land_type": self.basic_land_type,
            "mana_symbol": self.mana_symbol,
            "rule_id": self.RULE_ID,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "IntrinsicBasicLandManaSpec":
        expected_fields = {
            "kind",
            "schema_version",
            "basic_land_type",
            "mana_symbol",
            "rule_id",
        }
        if set(value) != expected_fields:
            raise IntrinsicBasicLandManaError(
                "intrinsic basic-land mana descriptor fields are invalid"
            )
        if value.get("kind") != "intrinsic_basic_land_mana":
            raise IntrinsicBasicLandManaError(
                "intrinsic basic-land mana descriptor kind is invalid"
            )
        if (
            type(value.get("schema_version")) is not int
            or value.get("schema_version") != cls.SCHEMA_VERSION
        ):
            raise IntrinsicBasicLandManaError(
                "intrinsic basic-land mana schema_version is unsupported"
            )
        if value.get("rule_id") != cls.RULE_ID:
            raise IntrinsicBasicLandManaError(
                "intrinsic basic-land mana rule_id is invalid"
            )
        basic_land_type = value.get("basic_land_type")
        mana_symbol = value.get("mana_symbol")
        if not isinstance(basic_land_type, str) or not isinstance(
            mana_symbol, str
        ):
            raise IntrinsicBasicLandManaError(
                "intrinsic basic-land mana subtype and symbol must be strings"
            )
        return cls(basic_land_type, mana_symbol)


def intrinsic_basic_land_mana_specs(
    type_line: str,
) -> tuple[IntrinsicBasicLandManaSpec, ...]:
    """Return CR 305.6 abilities from one current effective type line."""

    card_types, subtypes, _supertypes = type_parts(type_line)
    if "land" not in card_types:
        return ()
    return tuple(
        IntrinsicBasicLandManaSpec(subtype, symbol)
        for subtype, symbol in BASIC_LAND_MANA.items()
        if subtype in subtypes
    )


def expected_intrinsic_basic_land_mana_reminder(
    type_line: str,
) -> str | None:
    """Return the exact current Oracle reminder for a printed land type line."""

    specs = intrinsic_basic_land_mana_specs(type_line)
    if not specs:
        return None
    parts = _REMINDER_SEPARATOR.split(type_line, maxsplit=1)
    if len(parts) != 2:
        return None
    present = {spec.basic_land_type: spec for spec in specs}
    ordered = tuple(
        present[word.casefold()]
        for word in _TYPE_WORD.findall(parts[1])
        if word.casefold() in present
    )
    if len(ordered) != len(specs) or len(
        {value.basic_land_type for value in ordered}
    ) != len(specs):
        return None
    symbols = [f"{{{spec.mana_symbol}}}" for spec in ordered]
    if len(symbols) == 1:
        output = symbols[0]
    elif len(symbols) == 2:
        output = f"{symbols[0]} or {symbols[1]}"
    else:
        output = f"{', '.join(symbols[:-1])}, or {symbols[-1]}"
    return f"({{T}}: Add {output}.)"


__all__ = [
    "BASIC_LAND_MANA",
    "INTRINSIC_BASIC_LAND_MANA_CAPABILITY",
    "IntrinsicBasicLandManaError",
    "IntrinsicBasicLandManaSpec",
    "expected_intrinsic_basic_land_mana_reminder",
    "intrinsic_basic_land_mana_specs",
]
