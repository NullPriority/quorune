"""Runtime component boundary for an additive spell-copy replacement."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Mapping

from ..rules.additional_spell_copy import (
    ADDITIONAL_SPELL_COPY_CAPABILITY, ADDITIONAL_SPELL_COPY_COMPONENT,
    validate_additional_spell_copy_descriptor,
)
from ..replacement.spell_copy_occurrence import AdditionalSpellCopySource, COPY_EVENT
from .component_registry import RuntimeComponentRegistry
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class AdditionalSpellCopyHandler:
    handler_id: str = ADDITIONAL_SPELL_COPY_COMPONENT
    schema_version: int = 1
    family: str = "replacement.stack.spell-copy"
    event: str = COPY_EVENT
    rule_references: tuple[str, ...] = ("614.1a", "614.5", "616.1", "707.10")
    capability_dependencies: tuple[str, ...] = (ADDITIONAL_SPELL_COPY_CAPABILITY,)

    def validate(self, descriptor: Mapping) -> None:
        try:
            validate_additional_spell_copy_descriptor(descriptor)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        self.validate(descriptor)
        if not isinstance(context, AdditionalSpellCopySource):
            raise SemanticNodeError("An additional copy needs the effective original source")
        return (context,)


@lru_cache(maxsize=1)
def default_spell_copy_replacement_components():
    return RuntimeComponentRegistry((AdditionalSpellCopyHandler(),)).freeze()
