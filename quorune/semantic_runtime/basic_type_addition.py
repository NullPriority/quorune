from __future__ import annotations

from dataclasses import dataclass
from ..basic_type_addition_model import BASIC_TYPE_ADDITION_CAPABILITY,BASIC_TYPE_ADDITION_HANDLER,BasicTypeAdditionSpec
from ..continuous_effect_model import ContinuousEffect,ContinuousEffectOrigin,ContinuousEffectRelation,ContinuousOperation,Layer
from ..object_predicate import ObjectQuerySpec
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True,slots=True)
class ScopedBasicTypeAdditionHandler:
    handler_id:str=BASIC_TYPE_ADDITION_HANDLER
    schema_version:int=1
    family:str='continuous.basic_land_type.fixed_addition'
    event:str='characteristics.evaluate'
    source_component_layer:Layer=Layer.TYPE
    rule_references:tuple[str,...]=('305.6','305.7','400.7','611.3a','611.3b','613.1d','613.6')
    capability_dependencies:tuple[str,...]=(BASIC_TYPE_ADDITION_CAPABILITY,)

    def validate(self,descriptor):
        exact_fields(descriptor,{'handler_id','schema_version','event','addition'},field='basic type addition')
        if (descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int
            or descriptor['schema_version']!=1 or descriptor['event']!=self.event):
            raise SemanticNodeError('Basic type addition component identity changed')
        try:return BasicTypeAdditionSpec.from_dict(descriptor['addition'])
        except (ValueError,TypeError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self,descriptor,context):
        node=self.validate(descriptor)
        common={}
        if node.scope=='attached_land':
            if context.attached_object is None:return ()
            common={'relation':ContinuousEffectRelation.SOURCE_ATTACHED_TO_OBJECT,'related_object':context.attached_object}
        creature=node.schema_version==2
        query=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',) if creature else ('land',),
            token=False if creature else None,
            controller=context.source_controller if creature or node.scope=='controller_lands' else None)
        return (ContinuousEffect(effect_id=f'{context.source_object_id}:{context.component_id}',
            source_id=context.source_object_id,layer=Layer.TYPE,sublayer='4',timestamp=context.source_timestamp,
            operations=(*((ContinuousOperation('add_types',['Land'],field='card_types'),) if creature else ()),
                ContinuousOperation('add_types',list(node.basic_types),field='subtypes')),
            origin=ContinuousEffectOrigin.STATIC_ABILITY,applies=query,**common),)
