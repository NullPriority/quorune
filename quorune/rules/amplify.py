from __future__ import annotations

"""Typed Amplify specification and captured private hand-card facts."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..creature_subtypes import CREATURE_SUBTYPES


AMPLIFY_CAPABILITY = "zone.entry.amplify"
AMPLIFY_HANDLER = "replacement.zone.amplify-entry.v1"
AMPLIFY_REVEALS_FIELD = "amplify_hand_reveals"
_IDENTITY_FIELDS = ("object_id", "logical_object_id", "ref", "player", "oracle_id", "name")


@dataclass(frozen=True, slots=True)
class AmplifySpec:
    multiplier: int

    def __post_init__(self) -> None:
        if type(self.multiplier) is not int or self.multiplier < 0:
            raise ValueError("Amplify requires a fixed nonnegative multiplier")

    def to_dict(self) -> dict:
        return {"schema_version": 1, "multiplier": self.multiplier}

    @classmethod
    def from_dict(cls, value: Mapping) -> AmplifySpec:
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "multiplier"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Amplify requires a closed version-one specification")
        return cls(value["multiplier"])


@dataclass(frozen=True, slots=True)
class AmplifyHandCard:
    object_id: str
    logical_object_id: str
    ref: str
    player: str
    oracle_id: str
    name: str
    creature_types: tuple[str, ...]

    def __post_init__(self) -> None:
        if any(type(getattr(self, key)) is not str or not getattr(self, key) for key in _IDENTITY_FIELDS):
            raise ValueError("Amplify requires the complete private hand incarnation")
        if (type(self.creature_types) is not tuple or not self.creature_types
            or any(type(value) is not str or value not in CREATURE_SUBTYPES for value in self.creature_types)
            or self.creature_types != tuple(sorted(set(self.creature_types)))):
            raise ValueError("Amplify requires a canonical set of creature types")

    def reveal(self) -> dict[str, str]:
        return {key: getattr(self, key) for key in _IDENTITY_FIELDS}

    def to_dict(self) -> dict:
        return {**self.reveal(), "creature_types": list(self.creature_types)}

    @classmethod
    def from_dict(cls, value: Mapping) -> AmplifyHandCard:
        if (not isinstance(value, Mapping) or set(value) != {*_IDENTITY_FIELDS, "creature_types"}
            or not isinstance(value["creature_types"], (tuple, list))):
            raise ValueError("Amplify hand candidate fields are closed")
        return cls(**{key: value[key] for key in _IDENTITY_FIELDS},
            creature_types=tuple(value["creature_types"]))


def amplify_hand_reveals(payload: Mapping) -> tuple[Mapping, ...]:
    selected = payload.get(AMPLIFY_REVEALS_FIELD, ())
    if not isinstance(selected, (tuple, list)) or any(not isinstance(card, Mapping) for card in selected):
        raise ValueError("Prepared Amplify reveals must be an array of captured hand cards")
    return tuple(selected)
