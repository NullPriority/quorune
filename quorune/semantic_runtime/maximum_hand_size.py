from __future__ import annotations

"""Runtime validation for no-maximum-hand-size static components."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from ..maximum_hand_size import (
    NO_MAXIMUM_HAND_SIZE_CAPABILITY_ID,
    NO_MAXIMUM_HAND_SIZE_HANDLER_ID,
    MaximumHandSizeError,
    NoMaximumHandSizeSpec,
)
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class NoMaximumHandSizeHandler:
    handler_id: str = NO_MAXIMUM_HAND_SIZE_HANDLER_ID
    schema_version: int = 1
    family: str = "rule.cleanup.no_maximum_hand_size"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = ("402.2", "514.1", "613.1f")
    capability_dependencies: tuple[str, ...] = (
        NO_MAXIMUM_HAND_SIZE_CAPABILITY_ID,
    )

    def validate(self, descriptor: Mapping[str, Any]) -> NoMaximumHandSizeSpec:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "spec"},
            field="no-maximum-hand-size handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError(
                "No-maximum-hand-size handler ID mismatch"
            )
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError(
                "Unsupported no-maximum-hand-size handler version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                "No-maximum-hand-size handler must evaluate characteristics"
            )
        spec = descriptor["spec"]
        if not isinstance(spec, Mapping):
            raise SemanticNodeError(
                "No-maximum-hand-size spec must be an object"
            )
        try:
            return NoMaximumHandSizeSpec.from_dict(spec)
        except MaximumHandSizeError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self, descriptor: Mapping[str, Any], context: object
    ) -> tuple[NoMaximumHandSizeSpec, ...]:
        del context
        return (self.validate(descriptor),)


class NoMaximumHandSizeRegistry(
    RuntimeComponentRegistry[object, NoMaximumHandSizeSpec]
):
    pass


@lru_cache(maxsize=1)
def default_no_maximum_hand_size_registry() -> NoMaximumHandSizeRegistry:
    registry = NoMaximumHandSizeRegistry((NoMaximumHandSizeHandler(),))
    registry.require_registered_capabilities(load_default_capability_registry())
    return registry.freeze()


__all__ = [
    "NoMaximumHandSizeHandler",
    "NoMaximumHandSizeRegistry",
    "default_no_maximum_hand_size_registry",
]
