from __future__ import annotations

"""Closed spell-payment thresholds and their typed scalar references."""

from dataclasses import dataclass
from typing import Any, Mapping

PAID_MANA_CONDITION_CAPABILITY = 'resolution.effect.paid_mana_condition'
PAID_MANA_CONDITION_MECHANIC = 'fixed-paid-mana-resolution-condition'
CAST_MANA_UNITS_KIND = 'cast_mana_spent_units'


def validate_mana_type(value: Any) -> str:
    if type(value) is not str or value not in tuple('WUBRGC'):
        raise ValueError('Mana conditions require one concrete mana type')
    return value


@dataclass(frozen=True, slots=True)
class ManaSpentCondition:
    mana_type: str
    minimum: int
    kind: str = 'mana_spent_at_least'

    def __post_init__(self):
        validate_mana_type(self.mana_type)
        if self.kind!='mana_spent_at_least' or type(self.minimum) is not int or self.minimum<1:
            raise ValueError('Mana conditions require a fixed positive threshold')

    def to_dict(self):
        return {'schema_version':1,'kind':self.kind,'mana_type':self.mana_type,'minimum':self.minimum}

    def reference(self):
        return {'schema_version':1,'kind':CAST_MANA_UNITS_KIND,'mana_type':self.mana_type}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','kind','mana_type','minimum'}
                or type(value['schema_version']) is not int or value['schema_version']!=1):
            raise ValueError('Mana conditions use a closed version-one schema')
        return cls(value['mana_type'],value['minimum'],value['kind'])


def mana_type_from_reference(value):
    if (not isinstance(value,Mapping) or set(value)!={'schema_version','kind','mana_type'}
            or type(value['schema_version']) is not int or value['schema_version']!=1
            or value['kind']!=CAST_MANA_UNITS_KIND):
        raise ValueError('Mana count references use a closed version-one schema')
    return validate_mana_type(value['mana_type'])


def _paid_result_closed(effects,mechanics,*,same_target,controller):
    from .resolution_conditions import cast_fact_result_is_fixed
    controller_damage=bool(len(effects)==1 and effects[0].get('op')=='damage'
        and isinstance(effects[0].get('target'),Mapping)
        and effects[0]['target'].get('kind')=='target_controller_information')
    if cast_fact_result_is_fixed(effects,mechanics,allow_targeted_damage_source=same_target or controller_damage):return True
    from .query_effect_amount_model import PUBLIC_QUERY_AMOUNT_KIND
    return bool(not same_target and len(effects)==1 and effects[0].get('op')=='life'
        and effects[0].get('player') in {'$controller',controller} and 'public-query-effect-amount' in mechanics
        and (isinstance(effects[0].get('delta'),Mapping) and effects[0]['delta'].get('kind')==PUBLIC_QUERY_AMOUNT_KIND
            or type(effects[0].get('delta')) is int and effects[0]['delta']>=0))


def validate_paid_mana_instruction(effect):
    from .resolution_conditions import RESOLUTION_CONDITION_OPERATION,RESOLUTION_CONDITION_MECHANIC
    version=effect.get('schema_version');replacing=version==7
    expected={'op','schema_version','player','condition','cast_fact','effects','mechanic_ids','prefix_mechanic_ids'}
    if replacing:expected|={'otherwise_effects','otherwise_mechanic_ids'}
    if (type(version) is not int or version not in {6,7} or set(effect)!=expected
            or effect['op']!=RESOLUTION_CONDITION_OPERATION or type(effect['player']) is not str or not effect['player']):
        raise ValueError('Paid-mana result instructions use a closed versioned schema')
    condition=ManaSpentCondition.from_dict(effect['condition']);fact=effect['cast_fact']
    if isinstance(fact,Mapping):
        valid_fact=mana_type_from_reference(fact)==condition.mana_type
    else:valid_fact=type(fact) is int and fact>=0
    if not valid_fact:
        raise ValueError('Paid-mana results require their matching count reference or bound nonnegative count')
    for key in ('mechanic_ids','prefix_mechanic_ids',*(('otherwise_mechanic_ids',) if replacing else ())):
        values=effect[key]
        if (not isinstance(values,(list,tuple)) or any(type(v) is not str or not v for v in values)
                or len(set(values))!=len(values) or RESOLUTION_CONDITION_MECHANIC in values):
            raise ValueError('Paid-mana branch mechanics are malformed')
    for key,ids in (('effects','mechanic_ids'),*( (('otherwise_effects','otherwise_mechanic_ids'),) if replacing else ())):
        values=effect[key]
        if (not effect[ids] or not isinstance(values,(list,tuple)) or not 1<=len(values)<=8
                or any(not isinstance(v,Mapping) or v.get('op')==RESOLUTION_CONDITION_OPERATION for v in values)
                or not _paid_result_closed(values,effect[ids],same_target=replacing,controller=effect['player'])):
            raise ValueError('Paid-mana result branches require bounded independently represented children')
    return condition,tuple(effect['effects'])
