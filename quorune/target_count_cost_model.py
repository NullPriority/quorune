from __future__ import annotations

"""Source-pinned mandatory ordinary mana per chosen target beyond a threshold."""

from collections.abc import Mapping
from dataclasses import dataclass

from .repeated_mana_costs import FixedRepeatedManaCostSpec

TARGET_COUNT_COST_CAPABILITY = 'casting.additional_cost.per_target_fixed_mana'
TARGET_COUNT_COST_HANDLER = 'casting.additional.per-target-mana.v1'
TARGET_COUNT_COST_FIELD = 'target_count_cost'


@dataclass(frozen=True, slots=True)
class FixedManaPerTargetCostSpec:
    cost: FixedRepeatedManaCostSpec
    free_targets: int = 1
    schema_version: int = 1

    def __post_init__(self):
        if (not isinstance(self.cost, FixedRepeatedManaCostSpec)
                or type(self.free_targets) is not int or self.free_targets not in {0, 1}
                or type(self.schema_version) is not int or self.schema_version != 1):
            raise ValueError('Per-target mana cost is outside the fixed source family')

    def to_dict(self):
        return {'schema_version': self.schema_version, 'cost': self.cost.to_dict(),
            'free_targets': self.free_targets}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'cost', 'free_targets'}:
            raise ValueError('Per-target mana cost fields are malformed')
        return cls(FixedRepeatedManaCostSpec.from_dict(value['cost']), value['free_targets'], value['schema_version'])

    def requirements_for_count(self, count):
        if type(count) is not int or count < 0:
            raise ValueError('Chosen target count must be a nonnegative integer')
        return {key: amount * max(0, count-self.free_targets) for key, amount in self.cost.requirements.items()}
