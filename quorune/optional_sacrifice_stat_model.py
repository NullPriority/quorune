"""Explicit stats of a creature selected for a resolution-time cost."""
from collections.abc import Mapping
from dataclasses import dataclass
from .scalar_effect_amount_model import PaidCreatureStatSnapshot

RESOLUTION_PAID_STAT_KIND = 'resolution_paid_creature_stat'
RESOLUTION_PAID_STAT_CAPABILITY = 'quantity_expression.resolution_paid_creature_stat'
OPTIONAL_SACRIFICE_STAT_WRAPPER_VERSION = 13


@dataclass(frozen=True, slots=True)
class ResolutionPaidCreatureStatSpec:
    characteristic: str
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.characteristic) is not str or self.characteristic not in {'power', 'toughness'}):
            raise ValueError('Resolution sacrifice stats require an explicit power/toughness version')

    def to_dict(self):
        return {'kind': RESOLUTION_PAID_STAT_KIND, 'schema_version': self.schema_version,
                'characteristic': self.characteristic}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'kind', 'schema_version', 'characteristic'}
                or value['kind'] != RESOLUTION_PAID_STAT_KIND):
            raise ValueError('Resolution sacrifice stat fields are closed')
        return cls(value['characteristic'], value['schema_version'])

    def amount(self, snapshot):
        if not isinstance(snapshot, PaidCreatureStatSnapshot):
            raise ValueError('Resolution sacrifice quantity requires sealed current-incarnation stats')
        return max(0, getattr(snapshot, self.characteristic))
