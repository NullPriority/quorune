from __future__ import annotations

"""Closed usage limits for compiled triggered abilities."""

from dataclasses import dataclass
from typing import Any, Mapping

TRIGGER_ONCE_PER_TURN_CAPABILITY = "trigger.usage.once_per_turn"


@dataclass(frozen=True, slots=True)
class TriggerLimitSpec:
    schema_version: int = 1
    kind: str = "once_per_turn"

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported trigger-limit schema version")
        if self.kind != "once_per_turn":
            raise ValueError("Unsupported trigger usage limit")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "kind": self.kind}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TriggerLimitSpec:
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "kind"}:
            raise ValueError("Trigger limit requires exactly schema_version and kind")
        return cls(schema_version=value["schema_version"], kind=value["kind"])


def validate_program_trigger_limit(program: Any) -> None:
    if program.trigger_limit is None:
        return
    TriggerLimitSpec.from_dict(program.trigger_limit)
    if program.active_zone != "battlefield" or program.event in {"resolve", "activate"}:
        raise ValueError("Trigger limits require a battlefield triggered ability")
    if TRIGGER_ONCE_PER_TURN_CAPABILITY not in program.capability_dependencies:
        raise ValueError("Trigger limits require their usage capability dependency")
