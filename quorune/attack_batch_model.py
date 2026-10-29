"""Closed query and recipient scope for one qualified attack declaration."""

from dataclasses import dataclass
from collections.abc import Mapping

from .object_predicate import ObjectQuerySpec

QUALIFIED_ATTACK_BATCH_CAPABILITY='trigger.event.qualified_attack_batch'
QUALIFIED_ATTACK_BATCH_VARIANT='one_or_more_qualified_attack'
ATTACK_BATCH_CONDITION='qualified_attack_match_count'
ATTACK_BATCH_CONTEXT='declared_attacker_snapshots'


def validate_attack_characteristic_query(query):
    projected=ObjectQuerySpec(zones=('battlefield',),types_all=query.types_all,types_any=query.types_any,
        excluded_types=query.excluded_types,subtypes_all=query.subtypes_all,subtypes_any=query.subtypes_any,
        excluded_subtypes=query.excluded_subtypes,colors_all=query.colors_all,colors_any=query.colors_any,
        colorless=query.colorless,supertypes_all=query.supertypes_all,keywords_all=query.keywords_all,
        keywords_none=query.keywords_none,token=query.token,state_predicate=query.state_predicate,
        numeric_characteristic=query.numeric_characteristic,commander=query.commander)
    if query!=projected:
        raise ValueError('Attack subjects require a closed public creature characteristic query')
    return query


@dataclass(frozen=True,slots=True)
class QualifiedAttackBatchSpec:
    query:ObjectQuerySpec
    minimum:int=1
    grouping:str='declaration'
    exclude_source:bool=False
    schema_version:int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1:raise ValueError('Attack batch version must be1')
        if type(self.minimum) is not int or not 1<=self.minimum<=10:raise ValueError('Attack batch minimum must be1through10')
        if self.grouping not in {'declaration','player','player_or_planeswalker'}:raise ValueError('Unknown attack recipient grouping')
        if type(self.exclude_source) is not bool:raise ValueError('Attack source exclusion must be boolean')
        if not isinstance(self.query,ObjectQuerySpec):raise ValueError('Attack batch requires an immutable object query')
        validate_attack_characteristic_query(self.query)
        if 'creature' not in self.query.types_all:raise ValueError('Attack batches require the creature type gate')
        if self.query.state_predicate is not None and self.query.state_predicate.counter_name is not None and self.query.state_predicate.minimum_counter_count!=1:
            raise ValueError('Named attack counter qualification requires positive presence')
        if self.query.numeric_characteristic is not None and self.query.numeric_characteristic.characteristic.value not in {'power','toughness'}:
            raise ValueError('Attack numeric predicates require one characteristic')

    def to_dict(self):
        return {'schema_version':1,'query':self.query.to_dict(),'minimum':self.minimum,'grouping':self.grouping,'exclude_source':self.exclude_source}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','query','minimum','grouping','exclude_source'}:
            raise ValueError('Attack batch specification has a closed field set')
        return cls(**{**value,'query':ObjectQuerySpec.from_dict(value['query'])})


def attack_batch_conditions(value):
    result=[]
    def visit(node):
        if isinstance(node,Mapping):
            if node.get('field')==ATTACK_BATCH_CONDITION:
                if set(node)!={'field','op','value','attack_batch'} or node['op']!='gte' or type(node['value']) is not int:
                    raise ValueError('Attack count conditions require one closed numeric comparison')
                spec=QualifiedAttackBatchSpec.from_dict(node['attack_batch'])
                if node['value']!=spec.minimum:raise ValueError('Attack threshold differs from its immutable specification')
                result.append(spec)
            for child in node.values():visit(child)
        elif isinstance(node,(tuple,list)):
            for child in node:visit(child)
    visit(value)
    if len(result)>1:raise ValueError('An attack batch has one qualification and grouping')
    return tuple(result)
