from __future__ import annotations

"""Immutable, unbound consequence of one CR 603.12 fixed action."""

from dataclasses import dataclass
from typing import Any, Mapping

from .creature_power_damage_model import (
    CREATURE_POWER_DAMAGE_OPERATION, OCCURRENCE_POWER_DAMAGE_LKI,
    OCCURRENCE_POWER_DAMAGE_SOURCE, OCCURRENCE_POWER_DAMAGE_VERSION, occurrence_power_damage_in,
)
from .creature_subtypes import canonical_creature_subtype
from .replacement.immutable import FrozenMap, thaw_value
from .scalar_effect_amount_model import PaidCreatureStatSnapshot, ScalarAmountOrigin, scalar_amount_specs


REFLEXIVE_PROGRAM_KIND = 'reflexive_trigger_program'
REFLEXIVE_PAYMENT_MECHANIC = 'fixed-action-reflexive-trigger'
REFLEXIVE_PAYMENT_CAPABILITY = 'trigger.reflexive.fixed_action_result'
BOUND_REFLEXIVE_PROGRAM_VERSION = 2
SELECTED_SACRIFICE_REFLEXIVE_PROGRAM_VERSION = 3
BOUND_REFLEXIVE_PROGRAM_VERSIONS = frozenset({BOUND_REFLEXIVE_PROGRAM_VERSION, SELECTED_SACRIFICE_REFLEXIVE_PROGRAM_VERSION})
SELECTED_REFLEXIVE_SACRIFICE_CAPABILITY = 'trigger.reflexive.selected_sacrifice'
REFLEXIVE_PAID_CREATURE_CONTEXT = 'reflexive_paid_creature'


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
                and isinstance(child, Mapping) and type(child.get('schema_version')) is int
                and child['schema_version'] in BOUND_REFLEXIVE_PROGRAM_VERSIONS
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
        if type(self.schema_version) is not int or self.schema_version not in {1, *BOUND_REFLEXIVE_PROGRAM_VERSIONS}:
            raise ValueError('Reflexive trigger programs use a closed versioned schema')
        if self.uses_paid_creature and self.schema_version != SELECTED_SACRIFICE_REFLEXIVE_PROGRAM_VERSION:
            raise ValueError('Reflexive paid power requires a selected sacrifice program')
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

    @property
    def uses_paid_creature(self) -> bool:
        return any(spec.origin is ScalarAmountOrigin.REFLEXIVE_PAID_CREATURE
                   for spec in scalar_amount_specs(self.effects))

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
class ReflexivePaidCreatureSnapshot:
    stats: PaidCreatureStatSnapshot
    subtypes: tuple[str, ...]

    def __post_init__(self):
        if (not isinstance(self.stats, PaidCreatureStatSnapshot)
                or not isinstance(self.subtypes, (list, tuple))
                or any(canonical_creature_subtype(value) != value or type(value) is not str
                       for value in self.subtypes)
                or tuple(self.subtypes) != tuple(sorted(set(self.subtypes)))):
            raise ValueError('Reflexive payment requires sealed creature stats and canonical subtypes')
        object.__setattr__(self, 'subtypes', tuple(self.subtypes))

    def to_dict(self):
        return {'stats': self.stats.to_dict(), 'subtypes': list(self.subtypes)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'stats', 'subtypes'}:
            raise ValueError('Reflexive paid creature characteristics are unavailable or malformed')
        return cls(PaidCreatureStatSnapshot.from_dict(value['stats']), value['subtypes'])


@dataclass(frozen=True, slots=True)
class QueueReflexiveTriggerIntent:
    controller: str
    parent_stack_ref: str
    program: FrozenMap
    paid_creature: ReflexivePaidCreatureSnapshot | None = None

    def __post_init__(self):
        if any(type(value) is not str or not value for value in (self.controller, self.parent_stack_ref)):
            raise ValueError('Reflexive trigger creation requires its locked controller and parent frame')
        object.__setattr__(self, 'program', FrozenMap(self.program))
        program = ReflexiveTriggerProgram.from_dict(self.program)
        if (self.paid_creature is not None and not isinstance(self.paid_creature, ReflexivePaidCreatureSnapshot)
                or program.uses_paid_creature != (self.paid_creature is not None)):
            raise ValueError('Reflexive consequence and paid creature receipt do not agree')
