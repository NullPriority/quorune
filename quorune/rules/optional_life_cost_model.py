from __future__ import annotations

"""Closed optional life payments that reduce only one colored mana symbol."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib

from ..util import stable_json

OPTIONAL_LIFE_COST_CAPABILITY = 'casting.cost.optional_life_colored_reduction'
OPTIONAL_LIFE_COST_HANDLER = 'modification.cast-cost.optional-life-color.v1'
OPTIONAL_LIFE_COST_EVENT = 'cast.cost.modify'
OPTIONAL_LIFE_COST_FIELD = 'optional_life_reduction'


@dataclass(frozen=True, slots=True)
class OptionalLifeColorReductionSpec:
    color: str

    def __post_init__(self):
        if self.color not in ('W', 'U', 'B', 'R', 'G'):
            raise ValueError('Optional life reduction requires one ordinary color')

    def to_dict(self):
        return {'schema_version': 1, 'color': self.color, 'life': 2, 'reduction': 1}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'schema_version', 'color', 'life', 'reduction'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or type(value['life']) is not int or value['life'] != 2
                or type(value['reduction']) is not int or value['reduction'] != 1):
            raise ValueError('Optional life reduction has a closed fixed-payment schema')
        return cls(value['color'])


def optional_life_cost_amount(option):
    fact = option.get(OPTIONAL_LIFE_COST_FIELD)
    if fact is None:
        return 0
    if (not isinstance(fact, Mapping) or set(fact) != {'schema_version', 'base_option', 'sources'}
            or type(fact['schema_version']) is not int or fact['schema_version'] != 1
            or not isinstance(fact['base_option'], str) or not fact['base_option']
            or not isinstance(fact['sources'], (list, tuple)) or not fact['sources']):
        raise ValueError('Optional life payment identity is malformed')
    seen = set()
    for source in fact['sources']:
        if (not isinstance(source, Mapping) or set(source) != {'source', 'logical', 'program', 'cost'}
                or any(not isinstance(source[key], str) or not source[key] for key in ('source', 'logical', 'program'))):
            raise ValueError('Optional life provider identity is malformed')
        OptionalLifeColorReductionSpec.from_dict(source['cost'])
        identity = (source['source'], source['logical'], source['program'])
        if identity in seen:
            raise ValueError('One provider cannot be paid twice for the same spell')
        seen.add(identity)
    expected = fact['base_option'] + '+life-reduction:' + hashlib.sha256(stable_json(fact).encode('utf-8')).hexdigest()[:24]
    if option.get('id') != expected:
        raise ValueError('Optional life selection does not match its source identity')
    return 2 * len(seen)


def optional_life_base_option_id(option):
    return option[OPTIONAL_LIFE_COST_FIELD]['base_option'] if optional_life_cost_amount(option) else option.get('id')
