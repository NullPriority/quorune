"""Closed disjunctions of current controlled-permanent sacrifice predicates."""
from dataclasses import dataclass, replace
from collections.abc import Mapping
from .object_predicate import ObjectQuerySpec

SACRIFICE_UNION_CAPABILITY = 'casting.additional_cost.permanent_union'
_TYPES = {'artifact', 'battle', 'creature', 'enchantment', 'land', 'planeswalker'}


def controlled_permanent_predicate(**fields):
    return ObjectQuerySpec(zones=('battlefield',), controller='$actor', known_to_actor=True, **fields)


@dataclass(frozen=True, slots=True)
class SacrificePredicateUnion:
    predicates: tuple[ObjectQuerySpec, ...]
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1 or not isinstance(self.predicates, (tuple, list)):
            raise ValueError('Sacrifice predicate union requires its versioned collection')
        predicates = tuple(self.predicates)
        if not 2 <= len(predicates) <= 4 or any(not isinstance(p, ObjectQuerySpec) for p in predicates) or len(set(predicates)) != len(predicates):
            raise ValueError('Sacrifice union requires two through four distinct clauses')
        for query in predicates:
            if not isinstance(query, ObjectQuerySpec) or query.token not in (None, True):
                raise ValueError('Sacrifice union clause is unrepresented')
            if query != controlled_permanent_predicate(types_all=query.types_all, types_any=query.types_any, token=query.token):
                raise ValueError('Sacrifice union clauses reserve all other predicate fields')
            if not (query.types_all or query.types_any or query.token is True) or query.types_all and query.types_any:
                raise ValueError('Sacrifice union clauses require a type or token predicate')
            if not set((*query.types_all, *query.types_any)) <= _TYPES:
                raise ValueError('Sacrifice union admits only permanent card types')
        object.__setattr__(self, 'predicates', predicates)

    def to_dict(self):
        return {'schema_version': self.schema_version, 'predicates': [p.to_dict() for p in self.predicates]}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'predicates'} or not isinstance(value['predicates'], (list, tuple)):
            raise ValueError('Sacrifice union has a closed schema')
        return cls(tuple(ObjectQuerySpec.from_dict(p) for p in value['predicates']), value['schema_version'])

    def bound_predicates(self, actor):
        return tuple(replace(query, controller=actor) for query in self.predicates)


def validate_sacrifice_union_variant(version, operation, count, predicate, unblocked, count_parameter, union):
    if version != 5:
        if union is not None:raise ValueError('Sacrifice union requires zone-cost schema five')
        return
    if (operation != 'sacrifice_one' or count != 1 or unblocked or count_parameter is not None
            or predicate != controlled_permanent_predicate() or not isinstance(union, SacrificePredicateUnion)):
        raise ValueError('Zone-cost schema five requires one controlled-permanent sacrifice union')
