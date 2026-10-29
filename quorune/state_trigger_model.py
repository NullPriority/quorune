from __future__ import annotations

"""CR603.8 immutable public-state subscription; no pending latch state."""

from dataclasses import dataclass
from typing import Mapping
from .continuous_conditions import FixedPublicStateConditionSpec,FixedPublicStateConditionKind as K
from .characteristic_fragments import CharacteristicQuantityScope

STATE_TRIGGER_CAPABILITY='trigger.state.fixed_public_condition'
STATE_TRIGGER_MECHANIC='fixed-public-state-trigger'
STATE_TRIGGER_EVENT='state.condition'
STATE_TRIGGER_FIELD='fixed_public_state_trigger'
STATE_TRIGGER_IDENTITY='state_trigger_source_identity'


def _validate_condition(condition):
    if not isinstance(condition,FixedPublicStateConditionSpec):raise ValueError('State trigger requires a typed public condition')
    if condition.kind in {K.ALL,K.ANY}:
        for child in condition.terms:_validate_condition(child)
        return
    if condition.kind is K.CONTROLLER_CREATURE_NUMERIC_EXISTS:return
    quantity=condition.quantity
    if (condition.kind not in {K.QUERY_COUNT_AT_LEAST,K.QUERY_COUNT_AT_MOST}
            or quantity is None or quantity.scope is not CharacteristicQuantityScope.CONTROLLER_ZONE
            or quantity.query is None or quantity.query.zones!=('battlefield',)):
        raise ValueError('State trigger condition is outside current controlled battlefield queries')


@dataclass(frozen=True,slots=True)
class PublicStateTriggerSpec:
    condition: FixedPublicStateConditionSpec
    schema_version: int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1:raise ValueError('State trigger has one closed version')
        _validate_condition(self.condition)

    def to_dict(self):return {'schema_version':1,'condition':self.condition.to_dict()}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','condition'}:raise ValueError('State trigger fields are closed')
        return cls(FixedPublicStateConditionSpec.from_dict(value['condition']),value['schema_version'])

    def event_condition(self):
        return {'field':STATE_TRIGGER_FIELD,'op':'truthy','value':True,'state_trigger':self.to_dict()}

    @classmethod
    def from_condition(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'field','op','value','state_trigger'}
                or value['field']!=STATE_TRIGGER_FIELD or value['op']!='truthy' or value['value'] is not True):
            raise ValueError('State trigger requires its exact public subscription')
        return cls.from_dict(value['state_trigger'])

    @property
    def capabilities(self):
        from .characteristic_fragments import characteristic_quantity_capabilities
        from .object_predicate import object_query_capabilities
        numeric=any(c.kind is K.CONTROLLER_CREATURE_NUMERIC_EXISTS for c in self.condition.leaf_conditions())
        return tuple(sorted({STATE_TRIGGER_CAPABILITY,'trigger.placement.apnap',
            *characteristic_quantity_capabilities(self.condition.to_dict()),*object_query_capabilities(self.condition.to_dict()),
            *(('condition.creature.fixed_numeric_exists',) if numeric else ())}))
