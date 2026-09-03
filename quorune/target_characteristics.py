from __future__ import annotations

"""Immutable current characteristics used by direct-target legality."""

from dataclasses import dataclass
from typing import Any, Mapping

from .characteristic_evaluation import type_parts
from .targets import TargetGroup


def _numeric_characteristic(value: Any) -> int | None:
    if type(value) is int:
        return value
    normalized = str(value or "").strip()
    if not normalized or normalized.lstrip("-").isdigit() is False:
        return None
    return int(normalized)


@dataclass(frozen=True, slots=True)
class TargetCharacteristicSnapshot:
    types: frozenset[str] = frozenset()
    subtypes: frozenset[str] = frozenset()
    supertypes: frozenset[str] = frozenset()
    keywords: frozenset[str] = frozenset()
    colors: frozenset[str] = frozenset()
    mana_value: float = 0.0
    power: int | None = None
    toughness: int | None = None

    @classmethod
    def from_effective_data(
        cls,
        data: Mapping[str, Any],
    ) -> "TargetCharacteristicSnapshot":
        types, subtypes, supertypes = type_parts(
            str(data.get("type_line") or "")
        )
        return cls(
            types=frozenset(types),
            subtypes=frozenset(subtypes),
            supertypes=frozenset(supertypes),
            keywords=frozenset(
                str(value).casefold()
                for value in data.get("keywords", ())
            ),
            colors=frozenset(
                str(value).upper() for value in data.get("colors", ())
            ),
            mana_value=float(
                data.get("mana_value", data.get("cmc", 0)) or 0
            ),
            power=_numeric_characteristic(data.get("power")),
            toughness=_numeric_characteristic(data.get("toughness")),
        )

    @classmethod
    def from_row(
        cls,
        row: Mapping[str, Any],
    ) -> "TargetCharacteristicSnapshot":
        return cls(
            types=frozenset(
                str(value).casefold() for value in row.get("types", ())
            ),
            subtypes=frozenset(
                str(value).casefold() for value in row.get("subtypes", ())
            ),
            supertypes=frozenset(
                str(value).casefold() for value in row.get("supertypes", ())
            ),
            keywords=frozenset(
                str(value).casefold() for value in row.get("keywords", ())
            ),
            colors=frozenset(
                str(value).upper() for value in row.get("colors", ())
            ),
            mana_value=float(row.get("mana_value", 0) or 0),
            power=_numeric_characteristic(row.get("power")),
            toughness=_numeric_characteristic(row.get("toughness")),
        )

    def row_values(self) -> dict[str, Any]:
        return {
            "types": set(self.types),
            "subtypes": set(self.subtypes),
            "supertypes": set(self.supertypes),
            "keywords": set(self.keywords),
            "colors": set(self.colors),
            "mana_value": self.mana_value,
            "power": self.power,
            "toughness": self.toughness,
        }

    def matches(self, group: TargetGroup) -> bool:
        characteristics_match = group.matches_type_characteristics(
            types=self.types,
            subtypes=self.subtypes,
            supertypes=self.supertypes,
        ) and group.matches_keyword_characteristics(keywords=self.keywords)
        if not characteristics_match:
            return False
        numeric = group.numeric_characteristic
        return numeric is None or numeric.permits(
            power=self.power,
            toughness=self.toughness,
        )


__all__ = ["TargetCharacteristicSnapshot"]
