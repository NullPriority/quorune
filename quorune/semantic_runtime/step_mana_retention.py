"""Descriptor validation for the current static mana-retention owner."""
from dataclasses import dataclass
from functools import lru_cache
from ..rules.step_mana_retention import RETENTION_CAPABILITY, RETENTION_EVENT, RETENTION_HANDLER, validate_retention_descriptor
from .component_registry import RuntimeComponentRegistry
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class StepManaRetentionHandler:
    handler_id: str = RETENTION_HANDLER
    schema_version: int = 1
    family: str = "rule.mana.step_color_retention"
    event: str = RETENTION_EVENT
    rule_references: tuple[str,...] = ("106.4","500.4","611.3b")
    capability_dependencies: tuple[str,...] = (RETENTION_CAPABILITY,)

    def validate(self, descriptor):
        try:
            return validate_retention_descriptor(descriptor)
        except (TypeError,ValueError,KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)


@lru_cache(maxsize=1)
def step_mana_retention_registry():
    from ..rules.capabilities import load_default_capability_registry
    registry=RuntimeComponentRegistry((StepManaRetentionHandler(),))
    registry.require_registered_capabilities(load_default_capability_registry())
    return registry.freeze()
