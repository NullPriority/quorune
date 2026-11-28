"""A separate controlled untapped creature pays for each additional mode."""

from collections.abc import Mapping
from dataclasses import dataclass


ESCALATE_TAP_FIELD = "escalate_tap"
ESCALATE_TAP_CAPABILITY = "casting.escalate.fixed_creature_tap"
ESCALATE_MODE_IDS = ("mode_1", "mode_2", "mode_3")


def validate_escalate_schema(mode_ids, schema):
    if (not isinstance(schema, Mapping) or set(schema) != {"mode_count", "min_modes", "max_modes", "modes"}
            or any(type(schema[key]) is not int for key in ("mode_count", "min_modes", "max_modes"))
            or schema["mode_count"] != 1 or schema["min_modes"] != 1
            or schema["max_modes"] != len(mode_ids) or not isinstance(schema["modes"], Mapping)
            or tuple(schema["modes"]) != mode_ids):
        raise ValueError("Escalate must price one complete nonrepeating modal schema")


def escalate_extra_modes(mode_ids, selected):
    if not selected or tuple(mode for mode in mode_ids if mode in selected) != tuple(selected):
        raise ValueError("Escalate requires distinct printed modes in source order")
    return len(selected) - 1


@dataclass(frozen=True, slots=True)
class EscalateTapModeSpec:
    mode_ids: tuple[str, ...]
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or not isinstance(self.mode_ids, (tuple, list))
                or tuple(self.mode_ids) != ESCALATE_MODE_IDS):
            raise ValueError("Escalate tap requires its three ordered printed modes")
        object.__setattr__(self, "mode_ids", tuple(self.mode_ids))

    def to_dict(self):
        return {"schema_version": self.schema_version, "mode_ids": list(self.mode_ids)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "mode_ids"}:
            raise ValueError("Escalate tap fields are closed")
        return cls(value["mode_ids"], value["schema_version"])

    def validate_schema(self, schema):
        validate_escalate_schema(self.mode_ids, schema)

    def tap_count(self, selected):
        return escalate_extra_modes(self.mode_ids, selected)
