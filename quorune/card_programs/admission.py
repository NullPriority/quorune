from __future__ import annotations

"""Source-pinned admission for effects that materialize card behavior.

Some independently exact abilities can safely execute on an otherwise partial
card. Effects such as Morph, Unearth, and battlefield self-return are different:
they put the physical card into a state where its other abilities can
immediately matter. These effects therefore require a compiler-certified
complete card boundary rather than the legacy semantic-program compatibility
view.
"""

from dataclasses import dataclass
from typing import Any, Mapping


REQUIRES_COMPLETE_CARD_PROGRAM_FIELD = "requires_complete_card_program"
CARD_PROGRAM_ADMISSION_FIELD = "card_program_admission"


class CardProgramAdmissionError(ValueError):
    """A complete-card admission declaration or certificate is malformed."""


@dataclass(frozen=True, slots=True)
class CompleteCardProgramAdmission:
    oracle_ir_status: str
    material_residual_count: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise CardProgramAdmissionError(
                "Unsupported complete-card admission schema version"
            )
        if self.oracle_ir_status not in {"exact", "partial", "unresolved", "failed"}:
            raise CardProgramAdmissionError(
                "Complete-card admission has an invalid Oracle IR status"
            )
        if (
            type(self.material_residual_count) is not int
            or self.material_residual_count < 0
        ):
            raise CardProgramAdmissionError(
                "Complete-card admission residual count must be nonnegative"
            )

    @property
    def admitted(self) -> bool:
        return self.oracle_ir_status == "exact" and self.material_residual_count == 0

    @classmethod
    def from_oracle_ir(cls, ir: Any) -> "CompleteCardProgramAdmission":
        return cls(
            oracle_ir_status=str(getattr(ir, "status", "")),
            material_residual_count=len(tuple(getattr(ir, "material_residuals", ()))),
        )

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "CompleteCardProgramAdmission":
        if set(value) != {
            "schema_version",
            "oracle_ir_status",
            "material_residual_count",
        }:
            raise CardProgramAdmissionError(
                "Complete-card admission certificates have a closed shape"
            )
        return cls(
            schema_version=value["schema_version"],
            oracle_ir_status=value["oracle_ir_status"],
            material_residual_count=value["material_residual_count"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "oracle_ir_status": self.oracle_ir_status,
            "material_residual_count": self.material_residual_count,
        }


def descriptor_requires_complete_card_program(
    descriptor: Mapping[str, Any],
) -> bool:
    return descriptor.get(REQUIRES_COMPLETE_CARD_PROGRAM_FIELD) is True


def program_has_complete_card_program_admission(program: Any) -> bool:
    """Fail closed unless a requiring handler has a valid exact certificate."""

    handlers = getattr(program, "handlers", ())
    if not any(
        isinstance(descriptor, Mapping)
        and descriptor_requires_complete_card_program(descriptor)
        for descriptor in handlers
    ):
        return False
    raw = getattr(program, "provenance", {}).get(CARD_PROGRAM_ADMISSION_FIELD)
    if not isinstance(raw, Mapping):
        return False
    try:
        return CompleteCardProgramAdmission.from_dict(raw).admitted
    except CardProgramAdmissionError:
        return False


def program_has_declared_complete_source_admission(program: Any) -> bool:
    """Validate a compiler-declared whole-source boundary without a handler."""
    provenance = getattr(program, 'provenance', {})
    if provenance.get(REQUIRES_COMPLETE_CARD_PROGRAM_FIELD) is not True:
        return False
    raw = provenance.get(CARD_PROGRAM_ADMISSION_FIELD)
    if not isinstance(raw, Mapping):
        return False
    try:
        return CompleteCardProgramAdmission.from_dict(raw).admitted
    except CardProgramAdmissionError:
        return False


__all__ = [
    "CARD_PROGRAM_ADMISSION_FIELD",
    "CardProgramAdmissionError",
    "CompleteCardProgramAdmission",
    "descriptor_requires_complete_card_program",
    "program_has_complete_card_program_admission",
    "program_has_declared_complete_source_admission",
    "REQUIRES_COMPLETE_CARD_PROGRAM_FIELD",
]
