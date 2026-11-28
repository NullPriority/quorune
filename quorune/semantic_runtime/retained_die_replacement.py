"""Capability-bound static component for the extra die and ignored lowest roll."""

from dataclasses import dataclass
from functools import lru_cache

from ..rules.retained_die_roll import HANDLER, REPLACEMENT_CAPABILITY, REPLACEMENT_EVENT, replacement_descriptor
from .component_registry import RuntimeComponentRegistry
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class RetainedDieReplacementHandler:
    handler_id: str = HANDLER
    schema_version: int = 1
    event: str = REPLACEMENT_EVENT
    family: str = "replacement.random.controller_extra_ignore_lowest"
    rule_references: tuple[str, ...] = ("614.1a", "614.5", "616.1", "706.6")
    capability_dependencies: tuple[str, ...] = (REPLACEMENT_CAPABILITY,)

    def validate(self, descriptor):
        if descriptor != replacement_descriptor() or type(descriptor.get("schema_version")) is not int:
            raise SemanticNodeError("Extra die replacement requires its exact controller-scoped component")
        return self

    def lower(self, descriptor, context):
        self.validate(descriptor)
        return ()


@lru_cache(maxsize=1)
def default_retained_die_replacement_registry():
    return RuntimeComponentRegistry((RetainedDieReplacementHandler(),))
