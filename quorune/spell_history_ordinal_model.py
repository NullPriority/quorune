"""Closed ordinal predicates over each caster's matching spell history."""

from collections.abc import Mapping
from dataclasses import dataclass

from .public_history_query_model import PublicHistoryQuerySpec

QUALIFIED_SPELL_ORDINAL_FIELD='qualified_spell_cast_number'
QUALIFIED_SPELL_ORDINAL_CAPABILITY='trigger.event.qualified_spell_ordinal'


@dataclass(frozen=True,slots=True)
class QualifiedSpellOrdinalSpec:
    query:PublicHistoryQuerySpec
    number:int
    schema_version:int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1:raise ValueError('Qualified spell ordinal version must be1')
        if not isinstance(self.query,PublicHistoryQuerySpec) or self.query.event!='spell_cast' or self.query.relation!='controller' or self.query.reduction!='count':
            raise ValueError('Spell ordinals require a per-caster committed spell count query')
        if type(self.number) is not int or not 1<=self.number<=10:raise ValueError('Qualified spell ordinal must be one through ten')

    @property
    def capability_dependencies(self):return (QUALIFIED_SPELL_ORDINAL_CAPABILITY,*self.query.capability_dependencies)

    def to_condition(self):
        return {'field':QUALIFIED_SPELL_ORDINAL_FIELD,'op':'eq','value':self.number,'schema_version':1,'query':self.query.to_dict()}

    @classmethod
    def from_condition(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'field','op','value','schema_version','query'} or value['field']!=QUALIFIED_SPELL_ORDINAL_FIELD or value['op']!='eq':
            raise ValueError('Qualified spell ordinal condition has a closed equality schema')
        return cls(PublicHistoryQuerySpec.from_dict(value['query']),value['value'],value['schema_version'])


def qualified_spell_ordinal_dependencies(value):
    required=set()
    def visit(node):
        if isinstance(node,Mapping):
            if node.get('field')==QUALIFIED_SPELL_ORDINAL_FIELD:required.update(QualifiedSpellOrdinalSpec.from_condition(node).capability_dependencies)
            for child in node.values():visit(child)
        elif isinstance(node,(list,tuple)):
            for child in node:visit(child)
    visit(value)
    return tuple(sorted(required))
