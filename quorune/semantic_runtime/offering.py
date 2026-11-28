from __future__ import annotations

"""Register one closed Offering cost component in the existing casting registry."""

from dataclasses import dataclass

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..offering_model import OFFERING_CAPABILITY, OFFERING_FIELD, OFFERING_HANDLER, OfferingSpec
from .component_registry import exact_fields
from .context import SemanticNodeError


def offering_descriptor(spec: OfferingSpec) -> dict:
    return {"handler_id": OFFERING_HANDLER, "schema_version": 1, "event": "cast.cost",
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True, OFFERING_FIELD: spec.to_dict()}


@dataclass(frozen=True, slots=True)
class OfferingCostHandler:
    handler_id: str = OFFERING_HANDLER
    schema_version: int = 1
    family: str = OFFERING_CAPABILITY
    event: str = "cast.cost"
    rule_references: tuple[str, ...] = ("118.7", "601.2b", "601.2f", "601.2h", "702.48a", "702.48b", "702.48c")
    capability_dependencies: tuple[str, ...] = (OFFERING_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {"handler_id", "schema_version", "event",
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD, OFFERING_FIELD}, field="Offering")
        try:
            spec = OfferingSpec.from_dict(descriptor[OFFERING_FIELD])
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        if type(descriptor["schema_version"]) is not int or dict(descriptor) != offering_descriptor(spec):
            raise SemanticNodeError("Offering descriptor requires the closed current card-program authority")
        return spec

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)
