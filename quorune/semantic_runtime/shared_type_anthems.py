"""Live shared-type quantities over the existing recipient stat operation."""
from dataclasses import dataclass

from ..continuous_effect_model import (
    ContinuousEffect, ContinuousEffectOrigin, ContinuousEffectRelation, ContinuousObjectIdentity,
    ContinuousOperation, Layer,
)
from ..object_predicate import ObjectQuerySpec
from ..object_query import object_matches_query
from ..recipient_modifier_model import (
    RecipientQuantityKind, RecipientQuantitySpec, RECIPIENT_MODIFIER_OPERATION,
    RECIPIENT_SHARED_TYPE_CAPABILITY,
)
from ..shared_creature_type_model import (
    SharedCreatureTypeObject, canonical_creature_types, shared_type_objects,
)
from .component_registry import exact_fields
from .context import SemanticNodeError


SHARED_TYPE_ANTHEM_HANDLER = 'continuous.anthem.shared-creature-type.v1'


@dataclass(frozen=True, slots=True)
class SharedTypeAnthemHandler:
    handler_id: str = SHARED_TYPE_ANTHEM_HANDLER
    schema_version: int = 1
    family: str = 'continuous.recipient_shared_type_power_toughness_anthem'
    event: str = 'characteristics.evaluate'
    source_component_layer: Layer = Layer.POWER_TOUGHNESS
    rule_references: tuple[str, ...] = ('205.3m','400.7','611.3a','611.3b','613.4c')
    capability_dependencies: tuple[str, ...] = (RECIPIENT_SHARED_TYPE_CAPABILITY,)

    def validate(self, descriptor):
        from .continuous_components import FixedQueryPowerToughnessAnthemHandler
        exact_fields(descriptor,{'handler_id','schema_version','event','condition','modifier','relation'},field='shared-type anthem')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event):
            raise SemanticNodeError('Shared-type anthem identity is malformed')
        if descriptor['relation'] not in {'none','source_attached_to_object'}:
            raise SemanticNodeError('Shared-type anthems require an ordinary set or actual attachment')
        modifier=descriptor['modifier']
        exact_fields(modifier,{'schema_version','quantity','power','toughness'},field='shared-type modifier')
        if type(modifier['schema_version']) is not int or modifier['schema_version'] != 1:
            raise SemanticNodeError('Shared-type modifier descriptor version is malformed')
        try:quantity=RecipientQuantitySpec.from_dict(modifier['quantity'])
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
        if quantity.kind is not RecipientQuantityKind.SHARED_CREATURE_TYPES:
            raise SemanticNodeError('A shared-type anthem requires its world-relative quantity')
        base=FixedQueryPowerToughnessAnthemHandler().validate({
            'handler_id':'continuous.anthem.fixed-query.v2','schema_version':2,'event':self.event,
            'condition':descriptor['condition'],'modifier':{key:modifier[key] for key in ('power','toughness')}})
        if (base.target_controller != 'any' or base.exclude_source
                or base.predicate != ObjectQuerySpec(zones=('battlefield',),types_all=('creature',))):
            raise SemanticNodeError('Shared-type receivers require the complete battlefield creature domain')
        return base,quantity,ContinuousEffectRelation(descriptor['relation'])

    def lower(self, descriptor, context):
        base,quantity,relation=self.validate(descriptor)
        if relation is ContinuousEffectRelation.SOURCE_ATTACHED_TO_OBJECT and context.attached_object is None:
            return ()
        if context.public_type_objects is None:
            raise SemanticNodeError('Shared creature-type battlefield facts are unavailable')
        query=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),
            controller=context.source_controller if quantity.controller_relation=='source_controller' else None)
        objects=shared_type_objects(tuple(SharedCreatureTypeObject(
            ContinuousObjectIdentity(row.object_id,row.logical_object_id),canonical_creature_types(row.subtypes))
            for row in context.public_type_objects if object_matches_query(row,query)))
        modifier={'schema_version':2,'quantity':quantity.to_dict(),'power':base.power,'toughness':base.toughness,
                  'objects':[value.to_dict() for value in objects]}
        return (ContinuousEffect(effect_id=context.source_object_id+':'+context.component_id,
            source_id=context.source_object_id,layer=Layer.POWER_TOUGHNESS,sublayer='7c',
            timestamp=context.source_timestamp,origin=ContinuousEffectOrigin.STATIC_ABILITY,
            applies=base.predicate,relation=relation,
            related_object=context.attached_object if relation is ContinuousEffectRelation.SOURCE_ATTACHED_TO_OBJECT else None,
            operations=(ContinuousOperation(RECIPIENT_MODIFIER_OPERATION,modifier),)),)
