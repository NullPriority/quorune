"""Apply the paired graveyard redirection through the existing zone replacement registry."""

from dataclasses import dataclass

from ..continuous_effect_model import ContinuousObjectIdentity
from ..replacement.model import ReplacementClass, ReplacementEffect
from ..rules.linked_graveyard_play import (
    EXILE_LINK_FIELD, LINKED_ABILITY_EXILE_CAPABILITY, REPLACEMENT_CAPABILITY,
    REPLACEMENT_DEPENDENCIES, REPLACEMENT_EVENT, REPLACEMENT_HANDLER, LinkedGraveyardPlaySpec,
)
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class LinkedGraveyardExileReplacementHandler:
    handler_id: str = REPLACEMENT_HANDLER
    schema_version: int = 1
    family: str = "replacement.zone.destination"
    event: str = REPLACEMENT_EVENT
    rule_references: tuple[str, ...] = ("108.4", "607.2b", "614.1a", "614.6", "616.1")
    capability_dependencies: tuple[str, ...] = REPLACEMENT_DEPENDENCIES

    def validate(self, descriptor):
        try:
            return LinkedGraveyardPlaySpec.from_descriptor(descriptor, replacement=True)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def source_replacement_effect(self, descriptor, *, source_ref, source_controller, component_id, source_context=None):
        spec = self.validate(descriptor)
        if not isinstance(source_context, ContinuousObjectIdentity):
            raise SemanticNodeError("Linked graveyard exile requires the original current source incarnation")
        effect_id = f"{self.handler_id}:{source_ref}:{component_id}"
        return ReplacementEffect(
            effect_id=effect_id, source_id=source_ref, event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions=spec.conditions(source_controller),
            operations=(
                {"op": "set", "field": "destination", "value": "exile"},
                {"op": "set", "field": EXILE_LINK_FIELD, "value": {
                    "effect_id": effect_id, "source": source_context.to_dict(), "ability_pair": spec.ability_pair,
                }},
            ),
            label=f"{source_ref} exiles the card instead",
        )
