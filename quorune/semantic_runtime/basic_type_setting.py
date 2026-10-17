from __future__ import annotations

from dataclasses import dataclass
from ..basic_type_setting_model import BASIC_TYPE_SETTING_CAPABILITY,BASIC_TYPE_SETTING_HANDLER,BasicTypeSettingSpec
from ..continuous_effect_model import ContinuousEffect,ContinuousEffectOrigin,ContinuousEffectRelation,ContinuousOperation,Layer
from ..object_predicate import ObjectQuerySpec
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True,slots=True)
class FixedBasicTypeSettingHandler:
    handler_id:str=BASIC_TYPE_SETTING_HANDLER
    schema_version:int=1
    family:str='continuous.basic_land_type.fixed_setting'
    event:str='characteristics.evaluate'
    source_component_layer:Layer=Layer.TYPE
    rule_references:tuple[str,...]=('205.3i','305.6','305.7','305.8','400.7','613.1d','613.8','614.12')
    capability_dependencies:tuple[str,...]=(BASIC_TYPE_SETTING_CAPABILITY,)

    def validate(self,descriptor):
        exact_fields(descriptor,{'handler_id','schema_version','event','setting'},field='basic type setting')
        if (descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int
            or descriptor['schema_version']!=1 or descriptor['event']!=self.event):
            raise SemanticNodeError('Basic type setting component identity changed')
        try:return BasicTypeSettingSpec.from_dict(descriptor['setting'])
        except (ValueError,TypeError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self,descriptor,context):
        node=self.validate(descriptor);common={}
        if node.scope=='attached_land':
            if context.attached_object is None:return ()
            common={'relation':ContinuousEffectRelation.SOURCE_ATTACHED_TO_OBJECT,'related_object':context.attached_object}
        query=ObjectQuerySpec(zones=('battlefield',),types_all=('land',),
            controller=context.source_controller if node.scope=='controller_lands' else None,
            excluded_supertypes=('basic',) if node.scope=='nonbasic_lands' else ())
        return (ContinuousEffect(effect_id=f'{context.source_object_id}:{context.component_id}',
            source_id=context.source_object_id,layer=Layer.TYPE,sublayer='4',timestamp=context.source_timestamp,
            operations=(ContinuousOperation('set_basic_land_types',list(node.basic_types)),),
            origin=ContinuousEffectOrigin.STATIC_ABILITY,applies=query,**common),)
