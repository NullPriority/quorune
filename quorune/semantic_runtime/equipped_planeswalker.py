"""Materialize the quoted ability of a currently equipped planeswalker."""

from dataclasses import dataclass

from ..ability_fragments import ability_fragment_to_dict
from ..continuous_effect_model import (
    ContinuousEffect, ContinuousEffectOrigin, ContinuousEffectRelation, ContinuousObjectIdentity, ContinuousOperation, Layer,
)
from ..object_predicate import ObjectQuerySpec
from ..rules.equipped_planeswalker import (
    EQUIPPED_PLANESWALKER_CAPABILITY, EQUIPPED_PLANESWALKER_DEPENDENCIES, EQUIPPED_PLANESWALKER_HANDLER,
    equipped_planeswalker_fragment,
)
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class EquippedPlaneswalkerHandler:
    handler_id: str = EQUIPPED_PLANESWALKER_HANDLER
    schema_version: int = 1
    family: str = EQUIPPED_PLANESWALKER_CAPABILITY
    event: str = "characteristics.evaluate"
    source_component_layer: Layer = Layer.ABILITY
    rule_references: tuple[str, ...] = ("301.5", "508.1b", "603.2", "611.3b", "613.1f")
    capability_dependencies: tuple[str, ...] = EQUIPPED_PLANESWALKER_DEPENDENCIES

    def validate(self, descriptor):
        try:
            return equipped_planeswalker_fragment(descriptor)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def may_affect_static_components(self, descriptor):
        self.validate(descriptor)
        return False

    def lower(self, descriptor, context):
        fragment = self.validate(descriptor)
        if context.attached_object is None or not context.public_type_objects:
            return ()
        return (ContinuousEffect(
            effect_id=f"{context.source_object_id}:{context.component_id}:6",
            source_id=context.source_object_id,
            timestamp=context.source_timestamp,
            layer=Layer.ABILITY,
            sublayer="6",
            origin=ContinuousEffectOrigin.STATIC_ABILITY,
            applies=ObjectQuerySpec(zones=("battlefield",)),
            relation=ContinuousEffectRelation.SOURCE_OBJECT,
            related_object=ContinuousObjectIdentity(context.source_object_id, context.source_logical_object_id),
            operations=(ContinuousOperation("add_ability_fragment", ability_fragment_to_dict(fragment)),),
        ),)
