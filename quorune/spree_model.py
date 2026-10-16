from __future__ import annotations

"""Fixed additional mana prices associated with distinct printed modes."""

from dataclasses import dataclass
from typing import Mapping

from .replacement.immutable import FrozenMap, thaw_value


SPREE_FIXED_MECHANIC = 'fixed-spree-mode-costs'
SPREE_FIXED_CAPABILITY = 'casting.additional_cost.spree_fixed_modes'
MANA_KEYS = ('GENERIC', 'W', 'U', 'B', 'R', 'G', 'C')


@dataclass(frozen=True, slots=True)
class SpreeModeCosts:
    mode_costs: FrozenMap
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported Spree cost version')
        if not isinstance(self.mode_costs, Mapping):
            raise ValueError('Spree prices require a mode map')
        expected = tuple('mode_' + str(index) for index in range(1, len(self.mode_costs) + 1))
        if len(expected) not in {2, 3, 4, 5} or tuple(self.mode_costs) != expected:
            raise ValueError('Spree prices require distinct ordered printed mode IDs')
        for cost in self.mode_costs.values():
            if (not isinstance(cost, Mapping) or set(cost) != set(MANA_KEYS)
                    or any(type(value) is not int or value < 0 for value in cost.values())):
                raise ValueError('Spree prices require canonical fixed mana vectors')
        object.__setattr__(self, 'mode_costs', FrozenMap(self.mode_costs))

    def to_dict(self):
        return {'schema_version': self.schema_version, 'mode_costs': thaw_value(self.mode_costs)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'mode_costs'}:
            raise ValueError('Spree price fields are missing or unknown')
        return cls(value['mode_costs'], value['schema_version'])

    def validate_schema(self, schema):
        if (not isinstance(schema, Mapping) or set(schema) != {'mode_count', 'min_modes', 'max_modes', 'modes'}
                or type(schema['mode_count']) is not int or schema['mode_count'] != 1
                or type(schema['min_modes']) is not int or schema['min_modes'] != 1
                or type(schema['max_modes']) is not int or schema['max_modes'] != len(self.mode_costs)
                or not isinstance(schema['modes'], Mapping) or tuple(schema['modes']) != tuple(self.mode_costs)):
            raise ValueError('Spree prices must match one complete nonrepeating modal schema')
