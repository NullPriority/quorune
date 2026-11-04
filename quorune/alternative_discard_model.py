"""Typed private discard alternative over existing object-query predicates."""
from dataclasses import dataclass, replace
from collections.abc import Mapping
from .object_predicate import ObjectQuerySpec

ALTERNATIVE_DISCARD_KIND = 'alternative_discard_count'
ALTERNATIVE_DISCARD_CAPABILITY = 'choice.affected_player.alternative_discard'
ALTERNATIVE_DISCARD_MECHANIC = 'alternative-quality-discard'


@dataclass(frozen=True, slots=True)
class AlternativeDiscardCountSpec:
    qualifying_queries: tuple[ObjectQuerySpec, ...]
    fallback_count: int = 2
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported alternative discard count version')
        if type(self.fallback_count) is not int or self.fallback_count not in {2,3}:
            raise ValueError('Alternative discard fallback must be two or three')
        queries = tuple(self.qualifying_queries)
        if not 1 <= len(queries) <= 3 or len(set(queries)) != len(queries):
            raise ValueError('Alternative discard needs distinct bounded quality predicates')
        for query in queries:
            if not isinstance(query,ObjectQuerySpec) or query.token is not False:
                raise ValueError('Alternative discard predicates require hand cards')
            base = replace(query, types_all=(),types_any=(),excluded_types=(),supertypes_all=(),
                subtypes_any=(),keywords_all=(),token=None)
            if base != ObjectQuerySpec(zones=('hand',)):
                raise ValueError('Alternative discard has an unsupported hand predicate')
            if not any((query.types_all,query.types_any,query.excluded_types,query.supertypes_all,
                        query.subtypes_any,query.keywords_all)):
                raise ValueError('Alternative discard quality must be represented')
        object.__setattr__(self,'qualifying_queries',queries)

    def to_dict(self):
        return {'kind':ALTERNATIVE_DISCARD_KIND,'schema_version':self.schema_version,
            'fallback_count':self.fallback_count,'qualifying_queries':[q.to_dict() for q in self.qualifying_queries]}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'kind','schema_version','fallback_count','qualifying_queries'}
                or value['kind']!=ALTERNATIVE_DISCARD_KIND or not isinstance(value['qualifying_queries'],(list,tuple))):
            raise ValueError('Alternative discard count has a closed schema')
        return cls(tuple(ObjectQuerySpec.from_dict(q) for q in value['qualifying_queries']),
            value['fallback_count'],value['schema_version'])


def alternative_discard_count(value):
    return isinstance(value,Mapping) and value.get('kind')==ALTERNATIVE_DISCARD_KIND


def alternative_discard_runtime_dependencies(value):
    if not isinstance(value,Mapping) or not alternative_discard_count(value.get('count')):
        return ()
    AlternativeDiscardCountSpec.from_dict(value['count'])
    if (value.get('op')!='choose_cards_apnap' or value.get('zone')!='hand'
            or value.get('then')!='discard' or value.get('hidden') is not True):
        raise ValueError('Alternative discard count requires a private hand discard')
    return (ALTERNATIVE_DISCARD_CAPABILITY,)
