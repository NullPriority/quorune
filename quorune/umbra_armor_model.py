from __future__ import annotations

"""Source-local representation of the Aura's CR 702.89 static ability."""

from dataclasses import dataclass
from typing import Any, Mapping


UMBRA_ARMOR_FRAGMENT_CAPABILITY = "ability.umbra_armor.fragment"
UMBRA_ARMOR_CAPABILITY = "permanent.destroy.umbra_armor"
UMBRA_ARMOR_HANDLER = "ability.static.umbra_armor.v1"


@dataclass(frozen=True, slots=True)
class UmbraArmorSpec:
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Umbra armor has a closed source-local schema")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> UmbraArmorSpec:
        if not isinstance(value, Mapping) or set(value) != {"schema_version"}:
            raise ValueError("Umbra armor fields are malformed")
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class UmbraArmorProtection:
    aura_object_id: str
    aura_ref: str
    aura_logical_object_id: str
    aura_controller: str
    recipient_object_id: str
    recipient_logical_object_id: str
    recipient_controller: str
    instance: int

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value for value in (
            self.aura_object_id, self.aura_ref, self.aura_logical_object_id,
            self.aura_controller, self.recipient_object_id,
            self.recipient_logical_object_id, self.recipient_controller,
        )):
            raise ValueError("Umbra armor protection requires current source and recipient identity")
        if self.aura_object_id == self.recipient_object_id:
            raise ValueError("An Aura cannot enchant itself")
        if type(self.instance) is not int or self.instance < 0:
            raise ValueError("Umbra armor instance must be a nonnegative integer")
