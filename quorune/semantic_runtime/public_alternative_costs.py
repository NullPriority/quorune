from __future__ import annotations

"""Runtime descriptor ownership for fixed public alternative costs."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..public_alternative_costs import (
    FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY,
    FIXED_PUBLIC_ALTERNATIVE_COST_EVENT,
    FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID,
    FixedPublicAlternativeCostError,
    FixedPublicAlternativeCostSpec,
)
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class FixedPublicAlternativeCostHandler:
    handler_id: str = FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID
    schema_version: int = 1
    family: str = "casting.alternative-cost.fixed-public"
    event: str = FIXED_PUBLIC_ALTERNATIVE_COST_EVENT
    rule_references: tuple[str, ...] = (
        "118.9",
        "118.9a",
        "118.9b",
        "118.9c",
        "118.9d",
        "601.2b",
        "601.2f",
        "601.2h",
        "702.76a",
        "702.117a",
        "702.137a",
        "702.173a",
    )
    capability_dependencies: tuple[str, ...] = (
        FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY,
    )

    def validate(
        self,
        descriptor: Mapping[str, Any],
    ) -> FixedPublicAlternativeCostSpec:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                REQUIRES_COMPLETE_CARD_PROGRAM_FIELD,
                "alternative_cost",
            },
            field="fixed public alternative-cost handler",
        )
        if (
            descriptor["handler_id"] != self.handler_id
            or descriptor["schema_version"] != self.schema_version
            or descriptor["event"] != self.event
        ):
            raise SemanticNodeError(
                "Fixed public alternative-cost identity, version, or event changed"
            )
        if descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True:
            raise SemanticNodeError(
                "Fixed public alternative costs require complete-card admission"
            )
        try:
            return FixedPublicAlternativeCostSpec.from_dict(
                descriptor["alternative_cost"]
            )
        except (FixedPublicAlternativeCostError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[FixedPublicAlternativeCostSpec, ...]:
        del context
        return (self.validate(descriptor),)


class FixedPublicAlternativeCostRegistry(
    RuntimeComponentRegistry[object, FixedPublicAlternativeCostSpec]
):
    pass


@lru_cache(maxsize=1)
def default_fixed_public_alternative_cost_registry(
) -> FixedPublicAlternativeCostRegistry:
    registry = FixedPublicAlternativeCostRegistry(
        (FixedPublicAlternativeCostHandler(),)
    )
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()


__all__ = [
    "FixedPublicAlternativeCostHandler",
    "FixedPublicAlternativeCostRegistry",
    "default_fixed_public_alternative_cost_registry",
]
