from __future__ import annotations

"""Static modifiers whose scalar is evaluated separately for each recipient."""

from dataclasses import dataclass
from collections.abc import Mapping
from ..recipient_modifier_model import RECIPIENT_MODIFIER_CAPABILITY,RECIPIENT_MODIFIER_OPERATION,validate_recipient_modifier
from ..continuous_effect_model import ContinuousEffect,ContinuousOperation,ContinuousEffectOrigin,Layer
from .component_registry import exact_fields
from .context import SemanticNodeError

RECIPIENT_ANTHEM_HANDLER='continuous.anthem.recipient-quantity.v1'


@dataclass(frozen=True,slots=True)
class RecipientAnthemHandler:
    handler_id: str=RECIPIENT_ANTHEM_HANDLER
    schema_version: int=1
    family: str='continuous.recipient_quantity_power_toughness_anthem'
    event: str='characteristics.evaluate'
    source_component_layer: Layer=Layer.POWER_TOUGHNESS
    rule_references: tuple[str,...]=('105.2','107.4e','107.4f','109.5','122.1','611.3a','611.3b','613.4c')
    capability_dependencies: tuple[str,...]=('continuous.power_toughness.fixed_anthem',RECIPIENT_MODIFIER_CAPABILITY)

    def validate(self,descriptor):
        from .continuous_components import FixedQueryPowerToughnessAnthemHandler
        exact_fields(descriptor,{'handler_id','schema_version','event','condition','modifier'},field='recipient anthem')
        if descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int or descriptor['schema_version']!=1 or descriptor['event']!=self.event:
            raise SemanticNodeError('Recipient anthem identity is malformed')
        if not isinstance(descriptor['modifier'],Mapping) or descriptor['modifier'].get('schema_version')!=1:
            raise SemanticNodeError('Local recipient anthems require their version-one modifier')
        try:validate_recipient_modifier(descriptor['modifier'])
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
        modifier=descriptor['modifier']
        base=FixedQueryPowerToughnessAnthemHandler().validate({**descriptor,
            'handler_id':'continuous.anthem.fixed-query.v2','schema_version':2,
            'modifier':{key:modifier[key] for key in ('power','toughness')}})
        if 'creature' not in base.predicate.types_all:
            raise SemanticNodeError('Recipient anthems require an explicit creature subject')
        return base

    def lower(self,descriptor,context):
        from .continuous_components import _fixed_query_effect_predicate
        base=self.validate(descriptor)
        predicate=_fixed_query_effect_predicate(base.predicate,target_controller=base.target_controller,
            exclude_source=base.exclude_source,context=context)
        return (ContinuousEffect(effect_id=f'{context.source_object_id}:{context.component_id}',
            source_id=context.source_object_id,layer=Layer.POWER_TOUGHNESS,sublayer='7c',timestamp=context.source_timestamp,
            origin=ContinuousEffectOrigin.STATIC_ABILITY,applies=predicate,
            operations=(ContinuousOperation(RECIPIENT_MODIFIER_OPERATION,descriptor['modifier']),)),)
