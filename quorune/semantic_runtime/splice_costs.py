from __future__ import annotations

"""The trusted runtime descriptor for a printed hand Splice ability."""

from dataclasses import dataclass

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..rules.splice import SPLICE_CAPABILITY, SPLICE_HANDLER, SpliceSpec
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class SpliceCostHandler:
    handler_id: str = SPLICE_HANDLER
    schema_version: int = 1
    family: str = "casting.cost.splice"
    event: str = "cast.cost"
    rule_references: tuple[str, ...] = ("601.2b", "601.2c", "702.47a", "702.47b", "702.47c", "702.47d", "702.47e")
    capability_dependencies: tuple[str, ...] = (SPLICE_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor,
            {"handler_id", "schema_version", "event", REQUIRES_COMPLETE_CARD_PROGRAM_FIELD, "splice"},
            field="Splice casting component")
        if (descriptor["handler_id"] != self.handler_id or type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version or descriptor["event"] != self.event
            or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError("Splice casting component identity changed")
        try:
            return SpliceSpec.from_dict(descriptor["splice"])
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)
