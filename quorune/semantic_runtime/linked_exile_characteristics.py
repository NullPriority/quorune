from dataclasses import dataclass

from ..continuous_effect_model import ContinuousEffect, ContinuousEffectOrigin, ContinuousEffectRelation
from ..continuous_effect_model import ContinuousObjectIdentity, ContinuousOperation, Layer
from ..linked_exile_characteristic_model import CAPABILITY, HANDLER_ID, STATS_OPERATION, TYPE_OPERATION
from ..object_predicate import ObjectQuerySpec
from ..rules.linked_exile_characteristics import characteristic_link_binding
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class LinkedExileCharacteristicsHandler:
    handler_id: str = HANDLER_ID
    schema_version: int = 1
    family: str = CAPABILITY
    event: str = "characteristics.evaluate"
    source_component_layer: Layer = Layer.TYPE
    rule_references: tuple[str, ...] = ("607.2a", "611.3a", "613.1d", "613.4b")
    capability_dependencies: tuple[str, ...] = (CAPABILITY,)

    def validate(self, descriptor):
        try:
            return characteristic_link_binding(descriptor)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def may_affect_static_components(self, descriptor):
        self.validate(descriptor)
        return True

    def lower(self, descriptor, context):
        self.validate(descriptor)
        if not context.linked_characteristic_cards:
            return ()
        if not context.source_logical_object_id:
            raise SemanticNodeError("The linked characteristic setting needs the original source incarnation")
        scope = dict(source_id=context.source_object_id, timestamp=context.source_timestamp,
                     origin=ContinuousEffectOrigin.STATIC_ABILITY, applies=ObjectQuerySpec(zones=("battlefield",)),
                     relation=ContinuousEffectRelation.SOURCE_OBJECT,
                     related_object=ContinuousObjectIdentity(context.source_object_id, context.source_logical_object_id))
        values = tuple(identity.to_dict() for identity in context.linked_characteristic_cards)
        return tuple(ContinuousEffect(
            effect_id=f"{context.source_object_id}:{context.component_id}:{sublayer}", layer=layer, sublayer=sublayer,
            operations=(ContinuousOperation(operation, values),), **scope,
        ) for layer, sublayer, operation in (
            (Layer.TYPE, "4", TYPE_OPERATION), (Layer.POWER_TOUGHNESS, "7b", STATS_OPERATION),
        ))
