from __future__ import annotations

"""Immutable, unbound consequence of one CR 603.12 fixed action."""

from dataclasses import dataclass
from typing import Any, Mapping

from .replacement.immutable import FrozenMap, thaw_value


REFLEXIVE_PROGRAM_KIND = 'reflexive_trigger_program'
REFLEXIVE_PAYMENT_MECHANIC = 'fixed-action-reflexive-trigger'
REFLEXIVE_PAYMENT_CAPABILITY = 'trigger.reflexive.fixed_action_result'


def _values(value):
    if isinstance(value, Mapping):
        return (leaf for item in value.values() for leaf in _values(item))
    if isinstance(value, (list, tuple)):
        return (leaf for item in value for leaf in _values(item))
    return (value,)


@dataclass(frozen=True, slots=True)
class ReflexiveTriggerProgram:
    effects: tuple[FrozenMap, ...]
    target_schema: FrozenMap | None
    mechanic_ids: tuple[str, ...]

    def __post_init__(self):
        if (not isinstance(self.effects, (list, tuple)) or not 1 <= len(self.effects) <= 4
                or any(not isinstance(effect, Mapping) for effect in self.effects)):
            raise ValueError('Reflexive consequences require one complete typed body')
        object.__setattr__(self, 'effects', tuple(FrozenMap(effect) for effect in self.effects))
        if self.target_schema is not None:
            if not isinstance(self.target_schema, Mapping) or not self.target_schema:
                raise ValueError('Reflexive targets require a typed schema')
            object.__setattr__(self, 'target_schema', FrozenMap(self.target_schema))
        if (not isinstance(self.mechanic_ids, (list, tuple)) or not self.mechanic_ids
                or any(type(value) is not str or not value for value in self.mechanic_ids)
                or len(set(self.mechanic_ids)) != len(self.mechanic_ids)):
            raise ValueError('Reflexive consequence mechanics are malformed')
        object.__setattr__(self, 'mechanic_ids', tuple(self.mechanic_ids))
        if (any(effect.get('op') in {'offer_optional_effect', 'offer_optional_mana_payment'} for effect in self.effects)
                or any(isinstance(value, str) and value.startswith(('$context', '$event'))
                       for value in _values(self.effects))):
            raise ValueError('Reflexive consequences cannot nest payments or borrow an unrepresented antecedent')

    def to_dict(self):
        return {'kind': REFLEXIVE_PROGRAM_KIND, 'schema_version': 1,
            'effects': [thaw_value(effect) for effect in self.effects],
            'target_schema': thaw_value(self.target_schema) if self.target_schema is not None else None,
            'mechanic_ids': list(self.mechanic_ids)}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'kind', 'schema_version', 'effects', 'target_schema', 'mechanic_ids'}
                or value['kind'] != REFLEXIVE_PROGRAM_KIND or type(value['schema_version']) is not int
                or value['schema_version'] != 1):
            raise ValueError('Reflexive trigger programs use a closed versioned schema')
        return cls(effects=value['effects'], target_schema=value['target_schema'], mechanic_ids=value['mechanic_ids'])


@dataclass(frozen=True, slots=True)
class QueueReflexiveTriggerIntent:
    controller: str
    parent_stack_ref: str
    program: FrozenMap

    def __post_init__(self):
        if any(type(value) is not str or not value for value in (self.controller, self.parent_stack_ref)):
            raise ValueError('Reflexive trigger creation requires its locked controller and parent frame')
        object.__setattr__(self, 'program', FrozenMap(self.program))
        ReflexiveTriggerProgram.from_dict(self.program)
