"""One printed Casualty declaration and its sealed sacrifice payment fact."""
from collections.abc import Mapping
from dataclasses import dataclass
import re
from .power_sacrifice_model import power_sacrifice_predicate

CASUALTY_CAPABILITY = 'casting.additional_cost.casualty_fixed'
CASUALTY_HANDLER = 'casting.casualty.fixed-sacrifice.v1'
CASUALTY_FIELD = 'casualty_paid'
CASUALTY_SEMANTIC_KEY = 'builtin:casualty'


@dataclass(frozen=True, slots=True)
class FixedCasualtySpec:
    instance_id: str
    minimum_power: int
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1 or type(self.instance_id) is not str
                or re.fullmatch(r'[A-Za-z0-9:_-]+', self.instance_id) is None):
            raise ValueError('Casualty declaration identity is malformed')
        power_sacrifice_predicate(self.minimum_power)

    def to_dict(self):
        return {'schema_version': self.schema_version, 'instance_id': self.instance_id, 'minimum_power': self.minimum_power}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'instance_id', 'minimum_power'}:
            raise ValueError('Casualty declaration has a closed schema')
        return cls(**dict(value))

    def payment_fact(self, card):
        return validate_casualty_fact({**self.to_dict(), 'object': dict(card)})


def validate_casualty_fact(value):
    if not isinstance(value, Mapping) or set(value) != {'schema_version', 'instance_id', 'minimum_power', 'object'}:
        raise ValueError('Casualty payment fact has a closed schema')
    spec = FixedCasualtySpec.from_dict({key: value[key] for key in ('schema_version', 'instance_id', 'minimum_power')})
    card = value['object']
    if not isinstance(card, Mapping) or set(card) != {'ref', 'logical_object_id'} or any(type(v) is not str or not v for v in card.values()):
        raise ValueError('Casualty payment requires one retained creature incarnation')
    return {**spec.to_dict(), 'object': dict(card)}
