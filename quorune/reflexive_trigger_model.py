from __future__ import annotations

"""Immutable, unbound consequence of one CR 603.12 fixed action."""

from dataclasses import dataclass
from typing import Any, Mapping

from .creature_power_damage_model import (
    CREATURE_POWER_DAMAGE_OPERATION, OCCURRENCE_POWER_DAMAGE_LKI,
    OCCURRENCE_POWER_DAMAGE_SOURCE, OCCURRENCE_POWER_DAMAGE_VERSION, occurrence_power_damage_in,
)
from .replacement.immutable import FrozenMap, thaw_value


REFLEXIVE_PROGRAM_KIND = 'reflexive_trigger_program'
REFLEXIVE_PAYMENT_MECHANIC = 'fixed-action-reflexive-trigger'
REFLEXIVE_PAYMENT_CAPABILITY = 'trigger.reflexive.fixed_action_result'
BOUND_REFLEXIVE_PROGRAM_VERSION = 2


def _values(value):
    if isinstance(value, Mapping):
        return (leaf for item in value.values() for leaf in _values(item))
    if isinstance(value, (list, tuple)):
        return (leaf for item in value for leaf in _values(item))
    return (value,)


def bound_reflexive_has_targets(effects):
    for effect in effects:
        child = effect.get('reflexive_program')
        if (effect.get('op') == 'offer_optional_mana_payment' and effect.get('schema_version') == 4
                and isinstance(child, Mapping) and child.get('schema_version') == BOUND_REFLEXIVE_PROGRAM_VERSION
                and child.get('target_schema') is not None):
            return True
    return False


@dataclass(frozen=True, slots=True)
class ReflexiveTriggerProgram:
    effects: tuple[FrozenMap, ...]
    target_schema: FrozenMap | None
    mechanic_ids: tuple[str, ...]
    schema_version: int = 1

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
        if type(self.schema_version) is not int or self.schema_version not in {1, BOUND_REFLEXIVE_PROGRAM_VERSION}:
            raise ValueError('Reflexive trigger programs use a closed versioned schema')
        checked_effects = self.effects
        if self.schema_version == BOUND_REFLEXIVE_PROGRAM_VERSION and any(occurrence_power_damage_in(self.effects)):
            leaf = self.effects[0]
            if (len(self.effects) != 1 or leaf.get('op') != CREATURE_POWER_DAMAGE_OPERATION
                    or type(leaf.get('schema_version')) is not int
                    or leaf.get('schema_version') != OCCURRENCE_POWER_DAMAGE_VERSION
                    or leaf.get('source') != OCCURRENCE_POWER_DAMAGE_SOURCE
                    or leaf.get('source_lki') != OCCURRENCE_POWER_DAMAGE_LKI):
                raise ValueError('Reflexive occurrence programs require one sealed power damage result')
            checked_effects = ({key: value for key, value in leaf.items() if key not in {'source', 'source_lki'}},)
        if (any(effect.get('op') in {'offer_optional_effect', 'offer_optional_mana_payment'} for effect in self.effects)
                or any(isinstance(value, str) and value.startswith(('$context', '$event'))
                       for value in _values(checked_effects))):
            raise ValueError('Reflexive consequences cannot nest payments or borrow an unrepresented antecedent')

    def to_dict(self):
        return {'kind': REFLEXIVE_PROGRAM_KIND, 'schema_version': self.schema_version,
            'effects': [thaw_value(effect) for effect in self.effects],
            'target_schema': thaw_value(self.target_schema) if self.target_schema is not None else None,
            'mechanic_ids': list(self.mechanic_ids)}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'kind', 'schema_version', 'effects', 'target_schema', 'mechanic_ids'}
                or value['kind'] != REFLEXIVE_PROGRAM_KIND):
            raise ValueError('Reflexive trigger programs use a closed versioned schema')
        return cls(effects=value['effects'], target_schema=value['target_schema'],
                   mechanic_ids=value['mechanic_ids'], schema_version=value['schema_version'])


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
