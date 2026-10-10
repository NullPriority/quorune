from __future__ import annotations

"""Closed static control of a current Aura recipient."""

from dataclasses import dataclass
from typing import Any, Mapping

ATTACHED_CONTROL_CAPABILITY = "continuous.control.attached_source"
ATTACHED_CONTROL_HANDLER_ID = "continuous.control.attached-source.v1"


@dataclass(frozen=True, slots=True)
class AttachedControlSpec:
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported attached-control schema")

    def to_descriptor(self) -> dict[str, Any]:
        return {"handler_id":ATTACHED_CONTROL_HANDLER_ID,"schema_version":self.schema_version,
                "event":"characteristics.evaluate","controller":"source_controller"}

    @classmethod
    def from_descriptor(cls, value: Mapping[str, Any]) -> AttachedControlSpec:
        if not isinstance(value,Mapping) or set(value)!={'handler_id','schema_version','event','controller'}:
            raise ValueError("Attached control requires a closed descriptor")
        if value['handler_id']!=ATTACHED_CONTROL_HANDLER_ID or value['event']!='characteristics.evaluate' or value['controller']!='source_controller':
            raise ValueError("Attached control identity or controller changed")
        return cls(schema_version=value['schema_version'])
