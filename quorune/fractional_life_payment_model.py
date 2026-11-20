"""Explicit half-current-life resolution price, separate from fixed life amounts."""
from collections.abc import Mapping
from dataclasses import dataclass
from .fractional_quantity_model import HalfQuantitySpec

FRACTIONAL_LIFE_PAYMENT_KIND = 'half_current_payer_life'
FRACTIONAL_LIFE_PAYMENT_CAPABILITY = 'cost.life.half_current_resolution'
BOUND_FRACTIONAL_LIFE_PAYMENT_KIND = 'resolved_fractional_life'
BOUND_FRACTIONAL_LIFE_PAYMENT_VERSION = 13


@dataclass(frozen=True, slots=True)
class FractionalLifePaymentSpec:
    quantity: HalfQuantitySpec = HalfQuantitySpec('up')
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or not isinstance(self.quantity, HalfQuantitySpec) or self.quantity != HalfQuantitySpec('up')):
            raise ValueError('Fractional life prices require explicit upward-rounded half of current payer life')

    def amount(self, current_life):
        return self.quantity.amount(current_life)

    def to_dict(self):
        return {'kind': FRACTIONAL_LIFE_PAYMENT_KIND, 'schema_version': self.schema_version,
                'quantity': self.quantity.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'kind', 'schema_version', 'quantity'}
                or value.get('kind') != FRACTIONAL_LIFE_PAYMENT_KIND):
            raise ValueError('Fractional life price fields are closed')
        return cls(HalfQuantitySpec.from_dict(value['quantity']), value['schema_version'])


@dataclass(frozen=True, slots=True)
class ResolvedFractionalLifePayment:
    amount: int
    schema_version: int = BOUND_FRACTIONAL_LIFE_PAYMENT_VERSION

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != BOUND_FRACTIONAL_LIFE_PAYMENT_VERSION
                or type(self.amount) is not int or self.amount < 0):
            raise ValueError('Resolved fractional life price requires its explicit nonnegative version')

    def to_dict(self):
        return {'kind': BOUND_FRACTIONAL_LIFE_PAYMENT_KIND, 'schema_version': self.schema_version,
                'amount': self.amount}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'kind', 'schema_version', 'amount'}
                or value.get('kind') != BOUND_FRACTIONAL_LIFE_PAYMENT_KIND):
            raise ValueError('Resolved fractional life price fields are closed')
        return cls(value['amount'], value['schema_version'])
