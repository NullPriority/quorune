from __future__ import annotations

"""Immutable per-controller facts about names of successfully resolved spells."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from .replacement.immutable import FrozenMap, thaw_value
from .spell_cast_name_model import SpellCastNameHistory


def _names(value):
    if (not isinstance(value, tuple)
            or any(type(name) is not str or not name or name != name.strip() for name in value)
            or tuple(sorted(set(value))) != value):
        raise ValueError('Spell names must be canonical distinct English names')
    return value


@dataclass(frozen=True, slots=True)
class SpellResolutionNameHistory:
    names_by_controller: FrozenMap = field(default_factory=FrozenMap)
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported spell-resolution name history version')
        if not isinstance(self.names_by_controller, Mapping):
            raise ValueError('Resolution names require controller-scoped history')
        frozen = FrozenMap(self.names_by_controller)
        for controller, names in frozen.items():
            if type(controller) is not str or not controller:
                raise ValueError('Resolved names require a canonical controller')
            _names(names)
        object.__setattr__(self, 'names_by_controller', frozen)

    def to_dict(self):
        return {'schema_version': self.schema_version,
                'names_by_controller': thaw_value(self.names_by_controller)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'names_by_controller'}:
            raise ValueError('Resolution-name history has a closed schema')
        return cls(**value)

    def has_same_name_resolution(self, controller, names):
        if type(controller) is not str or not controller:
            raise ValueError('A resolution-name query requires its controller')
        return bool(set(_names(names)).intersection(self.names_by_controller.get(controller, ())))

    def note_resolution(self, *, stack_ref, controller, names):
        _names(names)
        first = bool(names) and not self.has_same_name_resolution(controller, names)
        fact = SpellResolutionNameFact(stack_ref, controller, names, first)
        if not names:return self, fact
        combined = tuple(sorted(set(self.names_by_controller.get(controller, ())) | set(names)))
        return SpellResolutionNameHistory(FrozenMap({**dict(self.names_by_controller), controller: combined})), fact


@dataclass(frozen=True, slots=True)
class SpellResolutionNameFact:
    stack_ref: str
    controller: str
    names: tuple[str, ...]
    first_named_resolution: bool | None
    schema_version: int = 1

    def __post_init__(self):
        _names(self.names)
        if (type(self.schema_version) is not int or self.schema_version != 1
                or any(type(value) is not str or not value for value in (self.stack_ref, self.controller))
                or self.first_named_resolution is not None and type(self.first_named_resolution) is not bool
                or not self.names and self.first_named_resolution is not False):
            raise ValueError('A resolution-name fact requires its original spell and exact known status')

    def to_dict(self):
        return {'schema_version': self.schema_version, 'stack_ref': self.stack_ref,
                'controller': self.controller, 'names': list(self.names),
                'first_named_resolution': self.first_named_resolution}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {
                'schema_version', 'stack_ref', 'controller', 'names', 'first_named_resolution'}
                or not isinstance(value['names'], (tuple, list))):
            raise ValueError('Resolution-name fact has a closed schema')
        return cls(**{**value, 'names': tuple(value['names'])})

    def require_known_first(self):
        if self.first_named_resolution is None:
            raise ValueError('Prior spell-name resolution history is unavailable')
        return self.first_named_resolution


def note_resolution_name(history, *, stack_ref, controller, names):
    if history is None:
        return None, SpellResolutionNameFact(stack_ref, controller, names, None if names else False)
    if not isinstance(history, SpellResolutionNameHistory):
        raise ValueError('Resolution history must be typed or explicitly unavailable')
    return history.note_resolution(stack_ref=stack_ref, controller=controller, names=names)


@dataclass(frozen=True, slots=True)
class SpellResolutionNameLedger:
    history: SpellResolutionNameHistory = field(default_factory=SpellResolutionNameHistory)
    pending: FrozenMap = field(default_factory=FrozenMap)
    schema_version: int = 1
    cast_history: SpellCastNameHistory | None = None

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version not in {1, 2}
                or not isinstance(self.history, SpellResolutionNameHistory) or not isinstance(self.pending, Mapping)):
            raise ValueError('A resolution ledger requires explicit known history and a closed version')
        if self.schema_version == 1 and self.cast_history is not None:
            raise ValueError('The original resolution ledger has no cast history')
        if self.schema_version == 2 and not isinstance(self.cast_history, SpellCastNameHistory):
            raise ValueError('The combined name ledger requires its separate cast history')
        frozen = FrozenMap(self.pending)
        for ref, value in frozen.items():
            fact = SpellResolutionNameFact.from_dict(value)
            if (ref != fact.stack_ref or fact.first_named_resolution is None
                    or not set(fact.names) <= set(self.history.names_by_controller.get(fact.controller, ()))):
                raise ValueError('Pending resolution claim does not match its retained named history')
        object.__setattr__(self, 'pending', frozen)

    def to_dict(self):
        return {'schema_version': self.schema_version, 'history': self.history.to_dict(),
                'pending': thaw_value(self.pending),
                **({'cast_history': self.cast_history.to_dict()} if self.cast_history is not None else {})}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping):
            raise ValueError('Spell-name ledger requires its versioned object')
        expected = {'schema_version', 'history', 'pending'}
        if value.get('schema_version') == 2:
            expected.add('cast_history')
        if set(value) != expected:
            raise ValueError('Spell-name ledger fields are closed')
        return cls(SpellResolutionNameHistory.from_dict(value['history']),
                   FrozenMap(value['pending']), value['schema_version'],
                   SpellCastNameHistory.from_dict(value['cast_history']) if 'cast_history' in value else None)

    def begin(self, *, stack_ref, controller, names):
        _names(names)
        previous = self.pending.get(stack_ref)
        if previous is not None:
            fact = SpellResolutionNameFact.from_dict(previous)
            if fact.controller != controller or fact.names != names:
                raise ValueError('A paused resolution changed its spell name or controller')
            return self, fact
        history, fact = self.history.note_resolution(stack_ref=stack_ref, controller=controller, names=names)
        pending = FrozenMap({**dict(self.pending), stack_ref: fact.to_dict()})
        return replace(self, history=history, pending=pending), fact

    def finish(self, stack_ref):
        if type(stack_ref) is not str or not stack_ref:
            raise ValueError('Completing resolution requires its original stack reference')
        if stack_ref not in self.pending:return self
        return replace(self, pending=FrozenMap({ref: fact for ref, fact in self.pending.items() if ref != stack_ref}))
