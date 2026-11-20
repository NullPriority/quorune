"""Closed amount records for an explicitly resolved current-payer life fraction."""
from collections.abc import Mapping
from dataclasses import dataclass
from ..fractional_life_payment_model import FractionalLifePaymentSpec, ResolvedFractionalLifePayment

FRACTIONAL_RECOVER_KIND = 'recover_half_current_life'
FRACTIONAL_RECOVER_CAPABILITY = 'keyword.recover.half_current_life'
FRACTIONAL_RECOVER_MECHANIC = 'recover-half-current-life'


@dataclass(frozen=True, slots=True)
class FractionalLifePaymentQuote:
    life: int
    schema_version: int = 1

    def __post_init__(self):
        if type(self.life) is not int or type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Fractional price quote requires exact signed current life')

    @property
    def payment(self):
        return ResolvedFractionalLifePayment(FractionalLifePaymentSpec().amount(self.life))

    def to_dict(self):
        return {'kind': 'fractional_life_payment_quote', 'schema_version': self.schema_version, 'life': self.life}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'kind', 'schema_version', 'life'}
                or value.get('kind') != 'fractional_life_payment_quote'):
            raise ValueError('Fractional price quote fields are closed')
        return cls(value['life'], value['schema_version'])


@dataclass(frozen=True, slots=True)
class FractionalLifeRecoverSpec:
    payment: FractionalLifePaymentSpec = FractionalLifePaymentSpec()
    schema_version: int = 2

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 2
                or not isinstance(self.payment, FractionalLifePaymentSpec)):
            raise ValueError('Half-life Recover requires its separate version-two price descriptor')

    def to_dict(self):
        return {'kind': FRACTIONAL_RECOVER_KIND, 'schema_version': self.schema_version, 'payment': self.payment.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'kind', 'schema_version', 'payment'}
                or value.get('kind') != FRACTIONAL_RECOVER_KIND):
            raise ValueError('Fractional Recover fields are closed')
        return cls(FractionalLifePaymentSpec.from_dict(value['payment']), value['schema_version'])


def resolved_fractional_payment_fields(value):
    if (not isinstance(value, Mapping)
            or set(value) != {'kind', 'schema_version', 'amount', 'requirements', 'predicate'}
            or value.get('requirements') is not None or value.get('predicate') is not None):
        raise ValueError('Resolved fractional payment requires one nonnegative life price without other costs')
    return ResolvedFractionalLifePayment.from_dict({k: value[k] for k in ('kind', 'schema_version', 'amount')})


def validate_resolved_fractional_payment_spec(spec):
    if (spec.kind != 'resolved_fractional_life' or spec.requirements is not None or spec.predicate is not None
            or spec.counter_name is not None or spec.source_ref is not None or spec.hybrid_symbols != ()):
        raise ValueError('Resolved fractional payment cannot carry a second resource or selector')
    return resolved_fractional_payment_fields(spec.to_dict())


def resolved_fractional_payment_is_payable(spec, current_life):
    payment = validate_resolved_fractional_payment_spec(spec)
    if type(current_life) is not int:
        raise ValueError('Fractional life payment requires an exact current life total')
    return payment.amount == 0 or current_life >= payment.amount


def fractional_payment_amount(spec, current_life):
    if not isinstance(spec, FractionalLifePaymentSpec):
        raise ValueError('Fractional payment requires its explicit current-payer origin')
    return ResolvedFractionalLifePayment(spec.amount(current_life))


def fractional_payment_record(payment):
    if not isinstance(payment, ResolvedFractionalLifePayment):
        raise ValueError('Fractional payment requires its explicit resolved price')
    record = {**payment.to_dict(), 'requirements': None, 'predicate': None}
    resolved_fractional_payment_fields(record)
    return record


def validate_fractional_payment_quote(quote, current_life):
    if not isinstance(quote, FractionalLifePaymentQuote) or type(current_life) is not int or current_life != quote.life:
        raise ValueError('Fractional payment current life changed since its offered price')
    return quote.payment


def _contains_fractional_price(value, *, resolved=False):
    if isinstance(value, Mapping):
        kinds = {'resolved_fractional_life'} if resolved else {'half_current_payer_life', FRACTIONAL_RECOVER_KIND, 'resolved_fractional_life'}
        return value.get('kind') in kinds or any(_contains_fractional_price(v, resolved=resolved) for v in value.values())
    return isinstance(value, (tuple, list)) and any(_contains_fractional_price(v, resolved=resolved) for v in value)


def fractional_life_payment_program_dependencies(program):
    values = (program.effects, getattr(program, 'handlers', ()), program.target_schema, getattr(program, 'cost_schema', None))
    if not _contains_fractional_price(values):return ()
    if _contains_fractional_price(values, resolved=True) or getattr(program, 'handlers', ()):
        raise ValueError('Bound fractional amounts are server-created cost facts, not declared program prices')
    from .recover import recover_program_dependencies
    required = recover_program_dependencies(program)
    if FRACTIONAL_RECOVER_CAPABILITY not in required:
        raise ValueError('Deferred life fraction lacks its complete represented price/result occurrence')
    return required
