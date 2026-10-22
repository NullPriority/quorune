from __future__ import annotations

"""Live layer-7c quantities over the existing validated anthem subject owner."""

from dataclasses import dataclass
from collections.abc import Mapping
from ..characteristic_fragments import CharacteristicQuantitySpec,CharacteristicQuantityScope as Scope
from ..object_predicate import ObjectQuerySpec
from ..continuous_effect_model import ContinuousEffect,ContinuousOperation,ContinuousEffectOrigin,Layer
from .context import SemanticNodeError
from .component_registry import exact_fields

QUANTIFIED_ANTHEM_HANDLER='continuous.anthem.live-quantity.v1'
QUANTIFIED_ANTHEM_CAPABILITY='continuous.power_toughness.live_quantity'


def recipient_is_in_quantity_domain(predicate,quantity):
    q=quantity.query
    if (quantity.scope is not Scope.ALL_ZONES or not isinstance(q,ObjectQuerySpec)
            or q.zones!=('battlefield',) or quantity.exclude_source or quantity.exclude_attached_object
            or quantity.schema_version not in {1,2} or quantity.reduction.value!='object_count'
            or quantity.multiplier!=1 or quantity.offset!=0):
        return False
    plain=ObjectQuerySpec(zones=('battlefield',),types_all=q.types_all,
        subtypes_all=q.subtypes_all,supertypes_all=q.supertypes_all)
    return (q==plain and set(q.types_all)<=set(predicate.types_all)
        and set(q.subtypes_all)<=set(predicate.subtypes_all)
        and set(q.supertypes_all)<=set(predicate.supertypes_all))


@dataclass(frozen=True,slots=True)
class QuantifiedAnthemHandler:
    handler_id: str=QUANTIFIED_ANTHEM_HANDLER
    schema_version: int=1
    family: str='continuous.quantified_query_power_toughness_anthem'
    event: str='characteristics.evaluate'
    source_component_layer: Layer=Layer.POWER_TOUGHNESS
    rule_references: tuple[str,...]=('107.1b','402.3','611.3a','611.3b','613.4c','613.8')
    capability_dependencies: tuple[str,...]=('continuous.power_toughness.fixed_anthem',QUANTIFIED_ANTHEM_CAPABILITY)

    def validate(self,descriptor):
        from .continuous_components import FixedQueryPowerToughnessAnthemHandler
        exact_fields(descriptor,{'handler_id','schema_version','event','condition','modifier'},field='quantified anthem')
        if (descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version']!=1 or descriptor['event']!=self.event):
            raise SemanticNodeError('Quantified anthem identity is malformed')
        modifier=descriptor['modifier']
        exact_fields(modifier,{'power','toughness','quantity','exclude_recipient'},field='quantified anthem modifier')
        if type(modifier['exclude_recipient']) is not bool:
            raise SemanticNodeError('Quantified recipient exclusion must be explicit')
        base=FixedQueryPowerToughnessAnthemHandler().validate({**descriptor,
            'handler_id':'continuous.anthem.fixed-query.v2','schema_version':2,
            'modifier':{key:modifier[key] for key in ('power','toughness')}})
        try:quantity=CharacteristicQuantitySpec.from_dict(modifier['quantity'])
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
        query=quantity.query
        if (quantity.scope not in {Scope.CONTROLLER_ZONE,Scope.OPPONENT_ZONES,Scope.ALL_ZONES}
                or query is None or quantity.exclude_source or quantity.exclude_attached_object
                or query.keywords_all or query.numeric_characteristic is not None):
            raise SemanticNodeError('Global modifiers require unambiguous layer-safe collection quantities')
        if query.zones==('hand',) and query!=ObjectQuerySpec(zones=('hand',)):
            raise SemanticNodeError('Global hand quantities expose only raw cardinality')
        if 'creature' not in base.predicate.types_all or base.target_controller=='source_owner':
            raise SemanticNodeError('Live anthems require an ordinary current creature subject')
        if modifier['exclude_recipient'] and not recipient_is_in_quantity_domain(base.predicate,quantity):
            raise SemanticNodeError('Other-object quantities must prove every affected recipient belongs to their domain')
        return base,quantity,modifier['exclude_recipient']

    def lower(self,descriptor,context):
        from .continuous_components import _fixed_query_effect_predicate
        base,_quantity,exclude=self.validate(descriptor)
        count=context.resolved_quantity
        if type(count) is not int or count<0:
            raise SemanticNodeError('Live anthem quantity is unavailable')
        # The validated subject is a subset of the count domain. Each recipient
        # therefore has exactly one member of that domain to exclude, including
        # when the ability source itself is not a member.
        if exclude:count=max(0,count-1)
        if not count:return ()
        predicate=_fixed_query_effect_predicate(base.predicate,target_controller=base.target_controller,
            exclude_source=base.exclude_source,context=context)
        return (ContinuousEffect(effect_id=f'{context.source_object_id}:{context.component_id}',
            source_id=context.source_object_id,layer=Layer.POWER_TOUGHNESS,sublayer='7c',
            timestamp=context.source_timestamp,origin=ContinuousEffectOrigin.STATIC_ABILITY,applies=predicate,
            operations=(ContinuousOperation('modify_power_toughness',[base.power*count,base.toughness*count]),)),)
