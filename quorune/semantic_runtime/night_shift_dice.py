"""Bind the current source ability that permits a life-paid dice adjustment."""

from dataclasses import dataclass
from functools import lru_cache

from ..rules.night_shift_dice import ADJUSTMENT_CAPABILITY, ADJUSTMENT_EVENT, HANDLER, adjustment_descriptor
from .component_registry import RuntimeComponentRegistry
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class NightShiftAdjustmentHandler:
    handler_id: str = HANDLER
    schema_version: int = 1
    event: str = ADJUSTMENT_EVENT
    family: str = ADJUSTMENT_CAPABILITY
    rule_references: tuple[str, ...] = ("119.4", "604.1", "706.2")
    capability_dependencies: tuple[str, ...] = (ADJUSTMENT_CAPABILITY,)

    def validate(self, descriptor):
        if descriptor != adjustment_descriptor() or type(descriptor.get("schema_version")) is not int:
            raise SemanticNodeError("A life-paid die adjustment requires its exact static component")
        return self

    def lower(self, descriptor, context):
        self.validate(descriptor)
        return ()


@lru_cache(maxsize=1)
def default_night_shift_adjustment_registry():
    return RuntimeComponentRegistry((NightShiftAdjustmentHandler(),))
