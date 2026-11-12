from __future__ import annotations

"""Closed pregame copy limits for one printed English card name."""

from dataclasses import dataclass
from typing import Any, Mapping


DECK_COPY_LIMIT_CAPABILITY = "format.commander.printed_copy_limit"
DECK_COPY_LIMIT_HANDLER_ID = "deck.construction.copy-limit.v1"
DECK_COPY_LIMIT_EVENT = "deck.construction"
DECK_COPY_LIMIT_TEMPLATE = "printed-deck-copy-limit-v1"


@dataclass(frozen=True, slots=True)
class DeckCopyLimitSpec:
    card_name: str
    maximum: int | None

    def __post_init__(self) -> None:
        if (type(self.card_name) is not str or not self.card_name or self.card_name != " ".join(self.card_name.split())
                or any(ord(character) < 32 for character in self.card_name)):
            raise ValueError("A deck copy limit requires one printed card name")
        if self.maximum is not None and (type(self.maximum) is not int or self.maximum < 1):
            raise ValueError("A finite deck copy limit must be a positive integer")

    def to_dict(self) -> dict[str, Any]:
        return {"card_name": self.card_name, "maximum": self.maximum}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DeckCopyLimitSpec:
        if not isinstance(value, Mapping) or set(value) != {"card_name", "maximum"}:
            raise ValueError("A deck copy limit has a closed descriptor")
        return cls(value["card_name"], value["maximum"])
