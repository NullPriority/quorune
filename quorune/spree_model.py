from __future__ import annotations

"""Fixed additional mana prices associated with distinct printed modes."""

from dataclasses import dataclass
from typing import Mapping

from .replacement.immutable import FrozenMap, thaw_value


SPREE_FIXED_MECHANIC = 'fixed-spree-mode-costs'
SPREE_FIXED_CAPABILITY = 'casting.additional_cost.spree_fixed_modes'
TIERED_FIELD = "tiered"
TIERED_VERSION = 2
TIERED_FIXED_MECHANIC = "fixed-tiered-mode-costs"
TIERED_FIXED_CAPABILITY = "casting.additional_cost.tiered_fixed_modes"
MODE_MANA_PRICE_FIELDS = frozenset({"spree", TIERED_FIELD})
MODE_MANA_PRICE_MECHANICS = frozenset({SPREE_FIXED_MECHANIC, TIERED_FIXED_MECHANIC})
MANA_KEYS = ('GENERIC', 'W', 'U', 'B', 'R', 'G', 'C')


@dataclass(frozen=True, slots=True)
class SpreeModeCosts:
    mode_costs: FrozenMap
    schema_version: int = 1
    mode_labels: FrozenMap | None = None

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version not in {1, TIERED_VERSION}:
            raise ValueError('Unsupported printed mode cost version')
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
        if self.schema_version == TIERED_VERSION:
            if (not isinstance(self.mode_labels, Mapping) or tuple(self.mode_labels) != expected
                    or any(type(label) is not str or not 1 <= len(label) <= 64 or " ".join(label.split()) != label
                           for label in self.mode_labels.values())):
                raise ValueError("Tiered prices require their ordered printed mode labels")
            object.__setattr__(self, "mode_labels", FrozenMap(self.mode_labels))
        elif self.mode_labels is not None:
            raise ValueError("Legacy priced modes have no printed label map")

    @property
    def field(self):
        return TIERED_FIELD if self.schema_version == TIERED_VERSION else "spree"

    @property
    def mechanic(self):
        return TIERED_FIXED_MECHANIC if self.schema_version == TIERED_VERSION else SPREE_FIXED_MECHANIC

    @property
    def capability(self):
        return TIERED_FIXED_CAPABILITY if self.schema_version == TIERED_VERSION else SPREE_FIXED_CAPABILITY

    @property
    def maximum_modes(self):
        return 1 if self.schema_version == TIERED_VERSION else len(self.mode_costs)

    def to_dict(self):
        value = {'schema_version': self.schema_version, 'mode_costs': thaw_value(self.mode_costs)}
        if self.mode_labels is not None:
            value["mode_labels"] = thaw_value(self.mode_labels)
        return value

    @classmethod
    def from_dict(cls, value):
        fields = {"schema_version", "mode_costs"}
        if isinstance(value, Mapping) and value.get("schema_version") == TIERED_VERSION:
            fields.add("mode_labels")
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ValueError("Printed mode price fields are missing or unknown")
        return cls(value["mode_costs"], value["schema_version"], value.get("mode_labels"))

    def validate_schema(self, schema):
        if (not isinstance(schema, Mapping) or set(schema) != {'mode_count', 'min_modes', 'max_modes', 'modes'}
                or type(schema['mode_count']) is not int or schema['mode_count'] != 1
                or type(schema['min_modes']) is not int or schema['min_modes'] != 1
                or type(schema['max_modes']) is not int or schema['max_modes'] != self.maximum_modes
                or not isinstance(schema['modes'], Mapping) or tuple(schema['modes']) != tuple(self.mode_costs)):
            raise ValueError('Printed mode prices must match the complete nonrepeating modal schema')


def fixed_mode_mana_costs(schema):
    if not isinstance(schema, Mapping):
        return None
    fields = MODE_MANA_PRICE_FIELDS.intersection(schema)
    if not fields:
        return None
    if len(fields) != 1:
        raise ValueError("Printed mode prices require a single cost policy")
    field, = fields
    prices = SpreeModeCosts.from_dict(schema[field])
    if prices.field != field:
        raise ValueError("Printed mode prices must use their exact policy field")
    return prices


def tiered_runtime_dependencies(program):
    schema = program.cost_schema
    if not (isinstance(schema, Mapping) and TIERED_FIELD in schema
            or TIERED_FIXED_MECHANIC in program.coverage):
        return ()
    prices = fixed_mode_mana_costs(schema)
    if (prices is None or prices.field != TIERED_FIELD or set(schema) != {TIERED_FIELD}
            or TIERED_FIXED_MECHANIC not in program.coverage
            or (program.event, program.active_zone) != ("resolve", "stack")
            or program.handlers or program.effects or program.event_condition is not None):
        raise ValueError("Tiered requires the complete one-mode spell and its associated prices")
    prices.validate_schema(program.target_schema)
    return (TIERED_FIXED_CAPABILITY,)
