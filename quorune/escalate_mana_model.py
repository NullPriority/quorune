"""One canonical mana vector is charged for each additional Escalate mode."""

from collections.abc import Mapping
from dataclasses import dataclass

from .escalate_tap_model import ESCALATE_MODE_IDS, escalate_extra_modes, validate_escalate_schema
from .replacement.immutable import FrozenMap, thaw_value
from .spree_model import MANA_KEYS


ESCALATE_MANA_FIELD = "escalate_mana"
ESCALATE_MANA_CAPABILITY = "casting.escalate.fixed_mana"


@dataclass(frozen=True, slots=True)
class EscalateManaModeSpec:
    mode_ids: tuple[str, ...]
    mana: FrozenMap
    schema_version: int = 1

    def __post_init__(self):
        if (
            type(self.schema_version) is not int or self.schema_version != 1
            or not isinstance(self.mode_ids, (list, tuple)) or tuple(self.mode_ids) != ESCALATE_MODE_IDS
            or not isinstance(self.mana, Mapping) or set(self.mana) != set(MANA_KEYS)
            or any(type(value) is not int or value < 0 for value in self.mana.values())
            or not any(self.mana.values())
        ):
            raise ValueError("Escalate mana requires a fixed nonzero vector and three ordered modes")
        object.__setattr__(self, "mode_ids", tuple(self.mode_ids))
        object.__setattr__(self, "mana", FrozenMap(self.mana))

    def to_dict(self):
        return {"schema_version": self.schema_version, "mode_ids": list(self.mode_ids), "mana": thaw_value(self.mana)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "mode_ids", "mana"}:
            raise ValueError("Escalate mana fields are closed")
        return cls(value["mode_ids"], value["mana"], value["schema_version"])

    def validate_schema(self, schema):
        validate_escalate_schema(self.mode_ids, schema)

    def additional_mana(self, selected):
        extra = escalate_extra_modes(self.mode_ids, selected)
        return {symbol: count * extra for symbol, count in self.mana.items() if count}
