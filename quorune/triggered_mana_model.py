from __future__ import annotations

"""Fixed contributions triggered by a permanent's own tap mana ability."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from .fixed_mana_abilities import FixedManaMode


FIXED_TRIGGERED_MANA_EVENT = "mana.tap.production"
FIXED_TRIGGERED_MANA_FIELD = "fixed_triggered_mana"
FIXED_TRIGGERED_MANA_CAPABILITY = "mana.triggered.fixed_output"
MANA_LAND_SUBTYPES = frozenset({"plains", "island", "swamp", "mountain", "forest"})


@dataclass(frozen=True, slots=True)
class FixedTriggeredManaSpec:
    scope: str
    subject: str
    output: FixedManaMode
    produced_color: str | None = None

    def __post_init__(self) -> None:
        if (type(self.scope) is not str or self.scope not in {"attached", "controller", "any"}
                or type(self.subject) is not str
                or self.subject not in {"permanent", "land", "creature", *MANA_LAND_SUBTYPES}
                or not isinstance(self.output, FixedManaMode)):
            raise ValueError("Unsupported fixed triggered mana subject or output")
        if self.scope == "attached" and (self.subject != "land" or self.produced_color is not None):
            raise ValueError("An attached mana trigger requires a land and unrestricted production type")
        if self.produced_color is not None and (
            type(self.produced_color) is not str or self.produced_color not in self.output.bundle
        ):
            raise ValueError("Unknown triggering mana type")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "scope": self.scope,
            "subject": self.subject,
            "output": {color: count for color, count in self.output.bundle.items() if count},
            "produced_color": self.produced_color,
        }

    @classmethod
    def from_dict(cls, value: Any) -> FixedTriggeredManaSpec:
        if (not isinstance(value, Mapping)
                or set(value) != {"schema_version", "scope", "subject", "output", "produced_color"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or not isinstance(value["output"], Mapping)):
            raise ValueError("Malformed fixed triggered mana descriptor")
        result = cls(value["scope"], value["subject"], FixedManaMode.from_bundle(value["output"]), value["produced_color"])
        if result.to_dict() != value:
            raise ValueError("Fixed triggered mana requires a canonical output")
        return result


def fixed_triggered_mana_spec(program: Any) -> FixedTriggeredManaSpec | None:
    condition = program.event_condition or {}
    if (program.event != FIXED_TRIGGERED_MANA_EVENT and FIXED_TRIGGERED_MANA_FIELD not in condition
            and FIXED_TRIGGERED_MANA_CAPABILITY not in program.capability_dependencies):
        return None
    if (program.event != FIXED_TRIGGERED_MANA_EVENT or program.active_zone != "battlefield"
            or set(condition) != {FIXED_TRIGGERED_MANA_FIELD}
            or program.target_schema is not None or program.cost_schema is not None
            or program.trigger_limit is not None or program.destination is not None
            or program.effects or program.handlers or program.requires_arbiter
            or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage):
        raise ValueError("Fixed triggered mana requires one current battlefield ability")
    return FixedTriggeredManaSpec.from_dict(condition[FIXED_TRIGGERED_MANA_FIELD])


def fixed_triggered_mana_runtime_dependencies(program: Any) -> tuple[str, ...]:
    return (FIXED_TRIGGERED_MANA_CAPABILITY,) if fixed_triggered_mana_spec(program) is not None else ()
