"""Closed resolving reductions and pending next-eligible-spell journal rules."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping

from .cast_cost_modifiers import CastCostAffectedController, PublicCastCostModifierSpec
from .continuous_effect_model import ContinuousEffectDuration, ContinuousEffectError
from .object_predicate import ObjectQuerySpec
from .query_effect_amount_model import PUBLIC_QUERY_AMOUNT_KIND, PUBLIC_QUERY_AMOUNT_CAPABILITY, PublicQueryAmountSpec
from .replacement.immutable import immutable_fingerprint

OPERATION='grant_next_spell_cost_reduction'
MECHANIC='next-eligible-spell-cost-reduction'
CAPABILITY='casting.cost.next_eligible_spell_reduction'
EFFECT_KIND='resolution_next_spell_cost_reduction'
RULES=('118.7a','601.2f','601.2h','601.2i','611.2a')


def validate_spell_query(value):
    query=ObjectQuerySpec.from_dict(value)
    PublicCastCostModifierSpec(CastCostAffectedController.SOURCE_CONTROLLER,(query,),-1)
    return query


def validate_next_spell_cost_instruction(value,*,resolved=False):
    if (not isinstance(value,Mapping) or set(value)!={'op','schema_version','spell_query','amount'}
            or value['op']!=OPERATION or type(value['schema_version']) is not int or value['schema_version']!=1):
        raise ValueError('Next-spell cost instructions have one closed schema')
    query=validate_spell_query(value['spell_query']);amount=value['amount'];dependencies={CAPABILITY}
    if type(amount) is int and amount>=0:return query,tuple(sorted(dependencies))
    if resolved or not isinstance(amount,Mapping):
        raise ValueError('A resolved next-spell reduction requires a nonnegative integer')
    from .scalar_effect_amount_model import SCALAR_AMOUNT_KIND, SCALAR_AMOUNT_CAPABILITY, ScalarAmountOrigin, ScalarEffectAmountSpec
    if amount.get('kind')==PUBLIC_QUERY_AMOUNT_KIND:
        spec=PublicQueryAmountSpec.from_dict(amount)
        if spec.schema_version!=1 or spec.coefficient!=1:
            raise ValueError('Next-spell query amounts are independent positive public values')
        dependencies.add(PUBLIC_QUERY_AMOUNT_CAPABILITY)
    elif amount.get('kind')==SCALAR_AMOUNT_KIND:
        spec=ScalarEffectAmountSpec.from_dict(amount)
        if (spec.schema_version!=1 or spec.origin is not ScalarAmountOrigin.SOURCE
                or spec.characteristic not in {'power','toughness'} or spec.coefficient!=1 or spec.binding_id is not None):
            raise ValueError('Next-spell scalar values require current-or-last-known source stats')
        dependencies.add(SCALAR_AMOUNT_CAPABILITY)
    else:raise ValueError('Next-spell amount producer is unsupported')
    return query,tuple(sorted(dependencies))


@dataclass(frozen=True,slots=True)
class ResolvedNextSpellCostReduction:
    effect_id: str
    source_id: str
    timestamp: int
    controller: str
    spell_query: ObjectQuerySpec
    amount: int
    duration: ContinuousEffectDuration=ContinuousEffectDuration.UNTIL_END_OF_TURN
    schema_version: int=1

    def __post_init__(self):
        if (any(type(v) is not str or not v for v in (self.effect_id,self.source_id,self.controller))
                or type(self.timestamp) is not int or self.timestamp<0
                or type(self.amount) is not int or self.amount<0
                or type(self.schema_version) is not int or self.schema_version!=1
                or self.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN
                or not isinstance(self.spell_query,ObjectQuerySpec)):
            raise ContinuousEffectError('Pending next-spell reduction has invalid identity, amount or duration')
        validate_spell_query(self.spell_query.to_dict())

    def to_dict(self):
        return {'effect_kind':EFFECT_KIND,'schema_version':self.schema_version,'effect_id':self.effect_id,
            'source_id':self.source_id,'timestamp':self.timestamp,'controller':self.controller,
            'spell_query':self.spell_query.to_dict(),'amount':self.amount,'duration':self.duration.value}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'effect_kind','schema_version','effect_id',
                'source_id','timestamp','controller','spell_query','amount','duration'} or value['effect_kind']!=EFFECT_KIND):
            raise ContinuousEffectError('Pending next-spell reduction fields are incomplete or unknown')
        return cls(value['effect_id'],value['source_id'],value['timestamp'],value['controller'],
            validate_spell_query(value['spell_query']),value['amount'],ContinuousEffectDuration(value['duration']),value['schema_version'])

    @property
    def fingerprint(self):return immutable_fingerprint(self.to_dict())


def next_spell_cost_node_capabilities(*,effects,target_schema,mechanic_ids,**kwargs):
    if MECHANIC not in mechanic_ids or target_schema is not None or len(effects)!=1:return ()
    try:return validate_next_spell_cost_instruction(effects[0])[1]
    except (TypeError,ValueError):return ()
