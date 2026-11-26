"""Layer-six grant of an exact linked exile activation source."""

from dataclasses import dataclass

from ..ability_fragments import ability_fragment_to_dict
from ..continuous_effect_model import ContinuousEffect, ContinuousEffectOrigin, ContinuousOperation, Layer
from ..granted_activation_model import LinkedExileActivatedGrant
from ..linked_exile_activation_model import (
    LINKED_EXILE_ACTIVATION_CAPABILITY, LINKED_EXILE_ACTIVATION_EVENT, LINKED_EXILE_ACTIVATION_HANDLER,
    descriptor_binding, validate_grant_pair,
)
from ..object_predicate import ObjectQuerySpec
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class LinkedExileActivatedGrantHandler:
    handler_id: str = LINKED_EXILE_ACTIVATION_HANDLER
    schema_version: int = 1
    family: str = LINKED_EXILE_ACTIVATION_CAPABILITY
    event: str = LINKED_EXILE_ACTIVATION_EVENT
    source_component_layer: Layer = Layer.ABILITY
    rule_references: tuple[str, ...] = ("201.5", "602.1", "607.2a", "607.5", "611.3b", "613.1f")
    capability_dependencies: tuple[str, ...] = (LINKED_EXILE_ACTIVATION_CAPABILITY, "zone.exile.linked_ability_pair")

    def validate(self, descriptor):
        try:
            return descriptor_binding(descriptor)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def may_affect_static_components(self, descriptor):
        self.validate(descriptor)
        return False

    def lower(self, descriptor, context):
        binding = self.validate(descriptor)
        pair = context.linked_ability_pair
        if not isinstance(pair, str) or pair != binding and not pair.startswith(binding + ":"):
            raise SemanticNodeError("A linked activation grant needs the current pair")
        fragment = LinkedExileActivatedGrant(
            context.source_object_id, context.source_logical_object_id, validate_grant_pair(pair),
        )
        return (ContinuousEffect(
            effect_id=f"{context.source_object_id}:{context.component_id}",
            source_id=context.source_object_id,
            layer=Layer.ABILITY,
            sublayer="6",
            timestamp=context.source_timestamp,
            operations=(ContinuousOperation("add_ability_fragment", ability_fragment_to_dict(fragment)),),
            origin=ContinuousEffectOrigin.STATIC_ABILITY,
            applies=ObjectQuerySpec(zones=("battlefield",), types_all=("artifact",), subtypes_all=("food",),
                                    controller=context.source_controller),
        ),)
