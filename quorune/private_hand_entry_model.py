from __future__ import annotations

"""Closed, versioned selection of a simultaneous private hand-entry group."""

from dataclasses import dataclass
from typing import Any, Mapping

from .hand_entry_queries import PERMANENT_TYPES
from .object_predicate import ObjectQuerySpec


PRIVATE_HAND_COLLECTION_CAPABILITY = "zone.move.private_hand_collection"
PRIVATE_HAND_COLLECTION_OPERATION = "put_cards_from_hand"
SELECTED_HAND_ENTRY_OPERATION = "move_selected_hand_cards"
PRIVATE_HAND_COLLECTION_MECHANICS = ("fixed-private-hand-entry", "fixed-public-zone-move")


def private_hand_collection_query(subject: str) -> ObjectQuerySpec | None:
    subject = " ".join(subject.casefold().split())
    if subject in {"creature", "land"}:
        return ObjectQuerySpec(zones=("hand",), types_all=(subject,))
    if subject in {"permanent", "non-saga permanent"}:
        return ObjectQuerySpec(zones=("hand",), types_any=PERMANENT_TYPES,
                               excluded_subtypes=("saga",) if subject.startswith("non-") else ())
    return None


@dataclass(frozen=True, slots=True)
class PrivateHandCollectionSpec:
    query: ObjectQuerySpec
    maximum: int | None = None
    tapped: bool = False

    def __post_init__(self):
        if not isinstance(self.query, ObjectQuerySpec) or self.query not in tuple(
            private_hand_collection_query(subject)
            for subject in ("creature", "land", "permanent", "non-saga permanent")
        ):
            raise ValueError("Private hand collection requires a closed hand predicate")
        if self.maximum is not None and (type(self.maximum) is not int or self.maximum != 2):
            raise ValueError("Private hand collection permits up to two or any number")
        if type(self.tapped) is not bool:
            raise ValueError("Private hand collection tapped policy must be boolean")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "query": self.query.canonical_dict(),
                "maximum": self.maximum, "tapped": self.tapped}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PrivateHandCollectionSpec:
        if (not isinstance(value, Mapping)
                or set(value) != {"schema_version", "query", "maximum", "tapped"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Private hand collection requires its complete version-one selection")
        return cls(ObjectQuerySpec.from_dict(value["query"]), value["maximum"], value["tapped"])


def decode_private_hand_collection(effect: Mapping[str, Any], *, actor: str = "$controller"):
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "player", "selection"}
            or effect["op"] != PRIVATE_HAND_COLLECTION_OPERATION or effect["player"] != actor):
        raise ValueError("Private hand collection effect is malformed")
    return PrivateHandCollectionSpec.from_dict(effect["selection"])
