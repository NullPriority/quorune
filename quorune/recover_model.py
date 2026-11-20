"""A closed fixed-mana Recover price using the existing resolution cost codec."""
from collections.abc import Mapping
from dataclasses import dataclass
from .fixed_effect_payment import FixedEffectPaymentSpec

RECOVER_KIND = 'recover_fixed_mana'
RECOVER_CAPABILITY = 'keyword.recover.fixed_mana'
RECOVER_MECHANIC = 'recover-fixed-mana'
RECOVER_PAYMENT_WRAPPER_VERSION = 12
RECOVER_RESULT_OPERATION = 'recover_source_result'
RECOVER_RESULT_CAPABILITY = 'zone.move.recover_source'


@dataclass(frozen=True, slots=True)
class RecoverSpec:
    payment: FixedEffectPaymentSpec
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or not isinstance(self.payment, FixedEffectPaymentSpec)
                or type(self.payment.schema_version) is not int or self.payment.schema_version != 2
                or self.payment.kind != 'mana' or type(self.payment.amount) is not int or self.payment.amount != 1):
            raise ValueError('Recover requires its explicit fixed ordinary mana price')

    def to_dict(self):
        return {'kind': RECOVER_KIND, 'schema_version': self.schema_version,
                'payment': self.payment.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'kind', 'schema_version', 'payment'}
                or value.get('kind') != RECOVER_KIND):
            raise ValueError('Recover descriptor fields are closed')
        return cls(FixedEffectPaymentSpec.from_dict(value['payment']), value['schema_version'])
