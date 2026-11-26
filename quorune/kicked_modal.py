"""A paid Kicker option relaxes the mode limit of one complete modal spell."""

from collections.abc import Mapping
from dataclasses import dataclass
from copy import deepcopy


KICKED_MODAL_FIELD = "kicked_modal"
KICKED_MODAL_CAPABILITY = "casting.kicker.fixed_modal_modes"


@dataclass(frozen=True, slots=True)
class KickedModalSpec:
    mode_ids: tuple[str, ...]
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported kicked-modal declaration")
        if not isinstance(self.mode_ids, (list, tuple)) or not 2 <= len(self.mode_ids) <= 5:
            raise ValueError("Kicked-modal declarations require a bounded printed mode list")
        canonical = tuple(f"mode_{index}" for index in range(1, len(self.mode_ids) + 1))
        if tuple(self.mode_ids) != canonical:
            raise ValueError("Kicked-modal modes must keep their printed order")
        object.__setattr__(self, "mode_ids", canonical)

    def to_dict(self):
        return {"schema_version": self.schema_version, "mode_ids": list(self.mode_ids)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "mode_ids"}:
            raise ValueError("Kicked-modal declaration fields are closed")
        return cls(value["mode_ids"], value["schema_version"])

    def validate_schema(self, schema):
        if (not isinstance(schema, Mapping) or set(schema) != {"mode_count", "min_modes", "max_modes", "modes"}
                or any(type(schema[key]) is not int or schema[key] != 1
                       for key in ("mode_count", "min_modes", "max_modes"))
                or not isinstance(schema["modes"], Mapping) or tuple(schema["modes"]) != self.mode_ids):
            raise ValueError("A kicked-modal spell starts from exactly one printed mode")

    def kicked_schema(self, schema):
        self.validate_schema(schema)
        return {**deepcopy(dict(schema)), "mode_count": 0, "min_modes": 0, "max_modes": len(self.mode_ids)}
