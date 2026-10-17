from __future__ import annotations

"""One printed fixed-mana Multikicker instance and its immutable payment fact."""

from collections.abc import Mapping
from dataclasses import dataclass

from .repeated_mana_costs import FixedRepeatedManaCostSpec, validate_repeated_mana_payment


MULTIKICKER_CAPABILITY = 'casting.multikicker.fixed_mana'
MULTIKICKER_ENTRY_CAPABILITY = 'replacement.multikicker.named_entry_counters'
MULTIKICKER_COST_HANDLER = 'casting.multikicker.fixed-mana.v1'
MULTIKICKER_ENTRY_HANDLER = 'replacement.zone.multikicker-counters.v1'
MULTIKICKER_FIELD = 'multikicker_paid'
MULTIKICKER_COUNT_FIELD = 'multikicker_count'
def validate_multikicker_fact(value):
    return validate_repeated_mana_payment(value)


@dataclass(frozen=True, slots=True)
class FixedManaMultikickerSpec(FixedRepeatedManaCostSpec):
    pass


@dataclass(frozen=True, slots=True)
class MultikickerEntrySpec:
    cost: FixedManaMultikickerSpec
    counter_name: str
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or not isinstance(self.cost, FixedManaMultikickerSpec) or type(self.counter_name) is not str
                or self.counter_name not in {'+1/+1', 'charge'}):
            raise ValueError('Multikicker entry counter is outside the closed family')

    def to_dict(self):
        return {'schema_version': self.schema_version, 'cost': self.cost.to_dict(),
            'counter_name': self.counter_name}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'cost', 'counter_name'}:
            raise ValueError('Multikicker entry fields are malformed')
        return cls(FixedManaMultikickerSpec.from_dict(value['cost']), value['counter_name'], value['schema_version'])

    def paid_count(self, fact):
        if fact is None:
            return 0
        paid = validate_multikicker_fact(fact)
        if paid['instance_id'] != self.cost.instance_id or paid['cost_fingerprint'] != self.cost.fingerprint:
            raise ValueError('Multikicker entry does not match its paid instance')
        return paid['count']
