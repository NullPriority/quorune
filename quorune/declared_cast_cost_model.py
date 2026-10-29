"""Versioned source-local declarations of optional additional casting costs."""
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import re

DECLARED_CAST_COSTS_FIELD = 'declared_cast_costs'
DECLARED_CAST_COST_CAPABILITY = 'casting.additional_cost.declared_instance'
DECLARED_CAST_COST_MECHANIC = 'declared-cast-cost-fact'


class DeclaredCastCostKind(str, Enum):
    BARGAIN = 'bargain'


@dataclass(frozen=True, slots=True)
class DeclaredCastCostChoice:
    kind: DeclaredCastCostKind
    instance_id: str
    chosen: bool

    def __post_init__(self):
        if not isinstance(self.kind, DeclaredCastCostKind) or type(self.chosen) is not bool:
            raise ValueError('Declared casting cost requires a known kind and boolean choice')
        if type(self.instance_id) is not str or re.fullmatch(r'[A-Za-z0-9:_-]+', self.instance_id) is None:
            raise ValueError('Declared casting cost requires its source-local instance')

    def to_dict(self):
        return {'kind': self.kind.value, 'instance_id': self.instance_id, 'chosen': self.chosen}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'kind', 'instance_id', 'chosen'}:
            raise ValueError('Declared casting choice has a closed schema')
        return cls(DeclaredCastCostKind(value['kind']), value['instance_id'], value['chosen'])


@dataclass(frozen=True, slots=True)
class DeclaredCastCostFacts:
    choices: tuple[DeclaredCastCostChoice, ...]
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1 or not isinstance(self.choices, (list, tuple)):
            raise ValueError('Declared casting facts require their versioned choices')
        choices = tuple(self.choices)
        if any(not isinstance(choice, DeclaredCastCostChoice) for choice in choices) or len({c.instance_id for c in choices}) != len(choices):
            raise ValueError('Declared casting facts have duplicated or untyped instances')
        object.__setattr__(self, 'choices', choices)

    def to_dict(self):
        return {'schema_version': self.schema_version, 'choices': [c.to_dict() for c in self.choices]}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'choices'} or not isinstance(value['choices'], (list, tuple)):
            raise ValueError('Declared casting facts have a closed schema')
        return cls(tuple(DeclaredCastCostChoice.from_dict(c) for c in value['choices']), value['schema_version'])

    def chosen(self, kind, instance_id):
        found = [c for c in self.choices if c.kind == kind and c.instance_id == instance_id]
        if len(found) != 1:raise ValueError('Linked declared casting instance is unavailable')
        return found[0].chosen

    def chosen_instances(self):
        return tuple(c.instance_id for c in self.choices if c.chosen)


def declared_cost_event_dependencies(condition):
    if not isinstance(condition, Mapping):return ()
    own = set()
    if condition.get('field') == 'declared_cast_cost_instances':
        values = condition.get('value')
        if condition.get('op') != 'contains_any' or not isinstance(values, (list, tuple)) or not values:
            raise ValueError('Declared casting event predicates require explicit linked instances')
        for value in values:DeclaredCastCostChoice(DeclaredCastCostKind.BARGAIN, value, True)
        if len(set(values)) != len(values):raise ValueError('Declared casting event instances are duplicated')
        own.add(DECLARED_CAST_COST_CAPABILITY)
    for key in ('all', 'any'):
        for child in condition.get(key, ()):own.update(declared_cost_event_dependencies(child))
    return tuple(sorted(own))


def declared_cost_copy_fields(context):
    if DECLARED_CAST_COSTS_FIELD not in context:return {}
    return {DECLARED_CAST_COSTS_FIELD: DeclaredCastCostFacts.from_dict(context[DECLARED_CAST_COSTS_FIELD]).to_dict()}
