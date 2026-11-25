from __future__ import annotations

"""Closed fixed self-entry counter components from compound keywords."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


FIXED_KEYWORD_ENTRY_CAPABILITY = "counter.producer.fixed_keyword_entry"
FIXED_KEYWORD_ENTRY_FRAGMENT = "fixed_keyword_entry_counter"
FIXED_KEYWORD_ENTRY_MECHANICS = frozenset(
    {
        "fading",
        "graft",
        "vanishing",
    }
)

_MECHANIC_FIELDS = {
    "fading": ("fade", "702.32a", "fading-fixed-entry-counter-v1"),
    "graft": ("+1/+1", "702.58a", "graft-fixed-entry-counter-v1"),
    "vanishing": ("time", "702.63a", "vanishing-fixed-entry-counter-v1"),
}


class FixedKeywordEntryCounterError(ValueError):
    """A fixed keyword entry-counter component is malformed."""


@dataclass(frozen=True, slots=True)
class FixedKeywordEntryCounterSpec:
    """One positive integral printed entry-counter component."""

    mechanic: str
    amount: int

    def __post_init__(self) -> None:
        mechanic = str(self.mechanic).casefold().strip()
        if mechanic not in FIXED_KEYWORD_ENTRY_MECHANICS:
            raise FixedKeywordEntryCounterError(
                "Fixed keyword entry counters require a supported mechanic"
            )
        if type(self.amount) is not int or self.amount < 1:
            raise FixedKeywordEntryCounterError(
                "Fixed keyword entry counters require a positive integer"
            )
        object.__setattr__(self, "mechanic", mechanic)

    @property
    def counter_name(self) -> str:
        return _MECHANIC_FIELDS[self.mechanic][0]

    @property
    def rule_id(self) -> str:
        return _MECHANIC_FIELDS[self.mechanic][1]

    @property
    def template_id(self) -> str:
        return _MECHANIC_FIELDS[self.mechanic][2]

    def to_dict(self) -> dict[str, Any]:
        return {"mechanic": self.mechanic, "amount": self.amount}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FixedKeywordEntryCounterSpec":
        if not isinstance(data, Mapping) or set(data) != {"mechanic", "amount"}:
            raise FixedKeywordEntryCounterError("Fixed keyword entry values have a closed shape")
        return cls(data["mechanic"], data["amount"])

    def handler_descriptor(self) -> dict[str, Any]:
        return {
            "handler_id": "replacement.zone.self-entry-counter.v1",
            "schema_version": 1,
            "event": "zone.change",
            "counter_name": self.counter_name,
            "amount": self.amount,
            "optional": False,
            "rule_id": self.rule_id,
        }


__all__ = [
    "FIXED_KEYWORD_ENTRY_CAPABILITY",
    "FIXED_KEYWORD_ENTRY_MECHANICS",
    "FixedKeywordEntryCounterError",
    "FixedKeywordEntryCounterSpec",
]
