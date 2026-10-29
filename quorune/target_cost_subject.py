from __future__ import annotations

"""Immutable public subjects for one target-dependent cost instance."""

from dataclasses import dataclass, replace
from typing import Mapping

from .object_predicate import ObjectQuerySpec


_PUBLIC_FIELDS = ('types_all', 'types_any', 'excluded_types', 'subtypes_all',
    'subtypes_any', 'excluded_subtypes', 'supertypes_all', 'excluded_supertypes',
    'colors_all', 'colors_any', 'colors_none', 'colorless', 'keywords_all', 'keywords_none')


@dataclass(frozen=True, slots=True)
class TargetCostSubjectSpec:
    relation: str
    predicate: ObjectQuerySpec = ObjectQuerySpec()
    controller_relation: str = 'any'

    def __post_init__(self):
        if type(self.relation) is not str or self.relation not in {'source', 'attached', 'query'}:
            raise ValueError('Target-cost subject relation is unsupported')
        if type(self.controller_relation) is not str or self.controller_relation not in {'any', 'source_controller'}:
            raise ValueError('Target-cost subject controller relation is unsupported')
        if not isinstance(self.predicate, ObjectQuerySpec):
            raise ValueError('Target-cost subject requires a typed public predicate')
        if self.relation != 'query':
            if self.predicate != ObjectQuerySpec() or self.controller_relation != 'any':
                raise ValueError('Relative target-cost subjects reserve their query fields')
            return
        empty = ObjectQuerySpec(zones=('battlefield',))
        defaults = {name: getattr(empty, name) for name in _PUBLIC_FIELDS}
        if replace(self.predicate, **defaults) != empty:
            raise ValueError('Target-cost query admits only current public characteristics')

    def to_dict(self):
        return {'relation': self.relation, 'predicate': self.predicate.to_dict(),
                'controller_relation': self.controller_relation}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'relation', 'predicate', 'controller_relation'}:
            raise ValueError('Target-cost subjects have a closed schema')
        return cls(value['relation'], ObjectQuerySpec.from_dict(value['predicate']), value['controller_relation'])
