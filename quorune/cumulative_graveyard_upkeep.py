from __future__ import annotations

"""Per-age graveyard choices preserve fixed-size cost units."""

from dataclasses import dataclass
from typing import Mapping

CAPABILITY = 'counter.producer.cumulative_upkeep_graveyard_library'
COST_CAPABILITY = 'cost.graveyard_to_library.grouped'
COST_FIELD = 'upkeep_graveyard_library_cost'
STATE_FIELD = 'graveyard_cost_state'
WRAPPER_VERSION = 11


@dataclass(frozen=True, slots=True)
class GraveyardLibraryCostUnit:
    graveyard_owner: str
    card_refs: tuple[str, ...]

    def __post_init__(self):
        if (type(self.graveyard_owner) is not str or not self.graveyard_owner
                or not isinstance(self.card_refs, tuple) or not self.card_refs
                or any(type(ref) is not str or not ref for ref in self.card_refs)
                or len(set(self.card_refs)) != len(self.card_refs)):
            raise ValueError('A graveyard cost unit requires one owner and distinct public cards')


@dataclass(frozen=True, slots=True)
class GraveyardLibraryCostSpec:
    unit_size: int = 2
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.unit_size) is not int or self.unit_size <= 0):
            raise ValueError('A grouped graveyard cost requires a positive fixed size')

    def to_dict(self):
        return {'schema_version': self.schema_version, 'unit_size': self.unit_size}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'unit_size'}:
            raise ValueError('Grouped graveyard costs have a closed specification')
        return cls(**value)

    def total_cards(self, age):
        if type(age) is not int or age < 0:
            raise ValueError('An upkeep age must be a nonnegative integer')
        return age * self.unit_size

    def effect(self):
        if self.unit_size != 2:
            raise ValueError('The current upkeep production represents two-card graveyard units')
        return {'op': 'offer_optional_mana_payment', 'schema_version': WRAPPER_VERSION,
                'player': '$controller', 'source': '$source.zone_object', COST_FIELD: self.to_dict()}

    @classmethod
    def from_effect(cls, value, *, actor='$controller', source='$source.zone_object'):
        if (not isinstance(value, Mapping) or set(value) != {'op', 'schema_version', 'player', 'source', COST_FIELD}
                or value['op'] != 'offer_optional_mana_payment' or type(value['schema_version']) is not int
                or value['schema_version'] != WRAPPER_VERSION or value['player'] != actor or value['source'] != source):
            raise ValueError('Grouped graveyard upkeep requires its closed version-eleven wrapper')
        spec = cls.from_dict(value[COST_FIELD])
        if spec.unit_size != 2:
            raise ValueError('Grouped graveyard upkeep requires two-card units')
        return spec

    @property
    def capabilities(self):
        return (CAPABILITY, COST_CAPABILITY, 'counter.producer.fixed_player_effect',
                'effect.choice.optional_fixed_mana_payment', 'trigger.source.fixed_maintenance',
                'zone.change.destination_replacement')

    def available_units(self, graveyards):
        pools = _graveyards(graveyards)
        return sum(len(refs) // self.unit_size for refs in pools.values())

    def can_pay(self, age, graveyards):
        self.total_cards(age)
        return self.available_units(graveyards) >= age

    def validate_units(self, age, units, graveyards):
        self.total_cards(age); pools = _graveyards(graveyards)
        if (not isinstance(units, tuple) or len(units) != age
                or any(not isinstance(unit, GraveyardLibraryCostUnit) for unit in units)):
            raise ValueError('The entire set of per-age graveyard cost choices is required')
        selected = set()
        for unit in units:
            if (len(unit.card_refs) != self.unit_size or unit.graveyard_owner not in pools
                    or not set(unit.card_refs) <= set(pools[unit.graveyard_owner])
                    or selected.intersection(unit.card_refs)):
                raise ValueError('A cost unit has wrong graveyard, size, stale or reused cards')
            selected.update(unit.card_refs)
        return units


def _graveyards(value):
    if not isinstance(value, Mapping):
        raise ValueError('Public graveyard pools must be owner-scoped')
    result = {}; seen = set()
    for owner, refs in value.items():
        if (type(owner) is not str or not owner or not isinstance(refs, tuple)
                or any(type(ref) is not str or not ref for ref in refs)
                or len(set(refs)) != len(refs) or seen.intersection(refs)):
            raise ValueError('Graveyard pools require distinct public references and known owners')
        result[owner] = refs; seen.update(refs)
    return result


def compile_graveyard_library_upkeep(text):
    import re
    if re.fullmatch(r"Cumulative upkeep[—–]\s*Put two cards from a single graveyard on the bottom of their owner's library\.?", text, re.I):
        return GraveyardLibraryCostSpec()
    return None
