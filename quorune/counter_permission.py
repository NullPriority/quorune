from __future__ import annotations

"""Immutable placement permissions, distinct from replacements and counter maxima."""

from collections.abc import Mapping
from dataclasses import dataclass

from .counter_names import normalized_counter_name


COUNTER_PROHIBITION_CAPABILITY = 'counter.placement.prohibition.static'
COUNTER_PROHIBITION_HANDLER = 'ability.static.counter-prohibition.v1'
COUNTER_PROHIBITION_EVENT = 'continuous'
_PERMANENT_TYPES = frozenset({'artifact', 'battle', 'creature', 'enchantment', 'land', 'planeswalker'})


class CounterPermissionError(ValueError):
    """A permission lacks the complete bounded facts needed to decide it."""


@dataclass(frozen=True, slots=True)
class CounterPlacementFact:
    subject_kind: str
    subject_id: str
    counter_name: str
    amount: int
    controller: str | None = None
    zone: str | None = None
    types: tuple[str, ...] = ()

    def __post_init__(self):
        if (type(self.subject_kind) is not str or self.subject_kind not in {'player', 'permanent'}
                or type(self.subject_id) is not str or not self.subject_id):
            raise CounterPermissionError('Counter placement needs a known subject')
        if type(self.amount) is not int or self.amount < 0:
            raise CounterPermissionError('Counter placement needs a nonnegative integer amount')
        if type(self.counter_name) is not str or not self.counter_name.strip():
            raise CounterPermissionError('Counter placement needs a named counter kind')
        object.__setattr__(self, 'counter_name', normalized_counter_name(self.counter_name))
        if not isinstance(self.types, tuple) or any(type(x) is not str or not x for x in self.types):
            raise CounterPermissionError('Counter placement needs typed current characteristics')
        if self.subject_kind == 'player':
            if self.controller is not None or self.zone is not None or self.types:
                raise CounterPermissionError('Player counter facts cannot name a permanent zone or types')
        elif type(self.zone) is not str or not self.zone:
            raise CounterPermissionError('Permanent counter facts require the represented zone')
        elif self.zone == 'battlefield' and (type(self.controller) is not str or not self.controller):
            raise CounterPermissionError('Battlefield counter facts require the current controller')


@dataclass(frozen=True, slots=True)
class FixedCounterProhibitionSpec:
    target_kind: str
    relation: str
    counter_name: str | None = None
    types_any: tuple[str, ...] = ()

    def __post_init__(self):
        if (type(self.target_kind) is not str or self.target_kind not in {'player', 'permanent'}
                or type(self.relation) is not str or self.relation not in {'any', 'you', 'opponent', 'self'}):
            raise CounterPermissionError('Unsupported counter prohibition domain')
        if (not isinstance(self.types_any, tuple)
                or any(type(x) is not str or x not in _PERMANENT_TYPES for x in self.types_any)
                or len(set(self.types_any)) != len(self.types_any)):
            raise CounterPermissionError('Counter prohibitions require a fixed public type disjunction')
        if self.target_kind == 'player' and (self.types_any or self.relation == 'self'):
            raise CounterPermissionError('Player prohibitions cannot use permanent identity or types')
        if self.relation == 'self' and self.types_any:
            raise CounterPermissionError('A self-reference does not depend on retaining its printed type')
        if self.counter_name is not None:
            if type(self.counter_name) is not str or not self.counter_name.strip():
                raise CounterPermissionError('Counter prohibitions require a literal counter kind')
            object.__setattr__(self, 'counter_name', normalized_counter_name(self.counter_name))

    def to_dict(self):
        return dict(schema_version=1, target_kind=self.target_kind, relation=self.relation,
            counter_name=self.counter_name, types_any=list(self.types_any))

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != {'schema_version', 'target_kind', 'relation', 'counter_name', 'types_any'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or not isinstance(value['types_any'], (list, tuple))):
            raise CounterPermissionError('Malformed counter prohibition')
        return cls(value['target_kind'], value['relation'], value['counter_name'], tuple(value['types_any']))

    def blocks(self, fact, *, source_object_id, source_controller):
        if not isinstance(fact, CounterPlacementFact):
            raise CounterPermissionError('Counter permissions require typed placement facts')
        if any(type(x) is not str or not x for x in (source_object_id, source_controller)):
            raise CounterPermissionError('Counter permissions require a current source identity and controller')
        if fact.amount == 0 or fact.subject_kind != self.target_kind:
            return False
        if self.counter_name is not None and fact.counter_name != self.counter_name:
            return False
        if fact.subject_kind == 'permanent':
            if fact.zone != 'battlefield':
                return False
            if self.types_any and not set(self.types_any).intersection(fact.types):
                return False
            if self.relation == 'self':
                return fact.subject_id == source_object_id
            controller = fact.controller
        else:
            controller = fact.subject_id
        return (self.relation == 'any' or self.relation == 'you' and controller == source_controller
            or self.relation == 'opponent' and controller != source_controller)


@dataclass(frozen=True, slots=True)
class CounterProhibitionRule:
    spec: FixedCounterProhibitionSpec
    source_object_id: str
    source_controller: str

    def __post_init__(self):
        if not isinstance(self.spec, FixedCounterProhibitionSpec):
            raise CounterPermissionError('Counter prohibition rules require a typed predicate')
        if any(type(x) is not str or not x for x in (self.source_object_id, self.source_controller)):
            raise CounterPermissionError('Counter prohibition rules require a current source')

    def to_dict(self):
        return dict(spec=self.spec.to_dict(), source_object_id=self.source_object_id,
            source_controller=self.source_controller)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'spec', 'source_object_id', 'source_controller'}:
            raise CounterPermissionError('Malformed counter prohibition source')
        return cls(FixedCounterProhibitionSpec.from_dict(value['spec']),
            value['source_object_id'], value['source_controller'])


@dataclass(frozen=True, slots=True)
class CounterPermissionSnapshot:
    rules: tuple[CounterProhibitionRule, ...] = ()

    def __post_init__(self):
        if not isinstance(self.rules, tuple) or any(not isinstance(x, CounterProhibitionRule) for x in self.rules):
            raise CounterPermissionError('Counter permissions require immutable typed rules')

    def allows(self, fact):
        if not isinstance(fact, CounterPlacementFact):
            raise CounterPermissionError('Counter permissions require typed placement facts')
        return not any(rule.spec.blocks(fact, source_object_id=rule.source_object_id,
            source_controller=rule.source_controller) for rule in self.rules)

    def to_dict(self):
        return dict(schema_version=1, rules=[rule.to_dict() for rule in self.rules])

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'schema_version', 'rules'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or not isinstance(value['rules'], (list, tuple))):
            raise CounterPermissionError('Malformed counter permission snapshot')
        return cls(tuple(CounterProhibitionRule.from_dict(rule) for rule in value['rules']))


ALLOW_ALL_COUNTERS = CounterPermissionSnapshot()


def counter_prohibition_payload_dependencies(value, *, coverage=()):
    if not isinstance(value, Mapping):
        return ()
    dependencies = ()
    if value.get('handler_id') == COUNTER_PROHIBITION_HANDLER:
        from .ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE, ability_fragment_from_dict
        if (set(value) != {'handler_id', 'schema_version', 'event', 'fragment'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or value['event'] != COUNTER_PROHIBITION_EVENT
                or not isinstance(ability_fragment_from_dict(value['fragment']), FixedCounterProhibitionSpec)):
            raise CounterPermissionError('Malformed counter prohibition descriptor')
        if CURRENT_ABILITY_FRAGMENT_COVERAGE not in coverage:
            raise CounterPermissionError('Counter prohibition requires current source-ability scope')
        dependencies = (COUNTER_PROHIBITION_CAPABILITY,)
    if value.get('op') == 'set' and value.get('field') == '_counter_permissions':
        CounterPermissionSnapshot.from_dict(value.get('value'))
        dependencies = (COUNTER_PROHIBITION_CAPABILITY,)
    return dependencies
