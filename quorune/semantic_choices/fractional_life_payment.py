"""Offer and accept one snapshotted life fraction through existing cost intents."""
from dataclasses import replace
from ..fixed_effect_payment import FixedEffectPaymentSpec
from ..fractional_life_payment_model import FractionalLifePaymentSpec
from ..replacement.immutable import FrozenMap
from ..rules.fractional_life_payment import (
    FractionalLifePaymentQuote, fractional_payment_record, validate_fractional_payment_quote,
)
from .fixed_effect_payment import prepare_fixed_payment_cost, complete_fixed_payment_cost
from .model import SemanticChoiceError

FRACTIONAL_LIFE_QUOTE_FIELD = '_fractional_life_quote'


def prepare_fractional_life_cost(spec, effect, context):
    if not isinstance(spec, FractionalLifePaymentSpec) or context.actor not in context.query.active_seats:
        raise SemanticChoiceError('Fractional life payment lost its active payer or price origin')
    quote = FractionalLifePaymentQuote(context.query.player_life(context.actor))
    resolved = FixedEffectPaymentSpec.from_dict(fractional_payment_record(quote.payment))
    prepared = prepare_fixed_payment_cost(resolved, effect, context)
    request = replace(prepared.request, prompt=f'Pay {quote.payment.amount} life or decline.',
        public_context=FrozenMap({**dict(prepared.request.public_context), 'life_cost': quote.payment.amount}))
    return replace(prepared, request=request,
        continuation_effect=FrozenMap({**dict(prepared.continuation_effect), FRACTIONAL_LIFE_QUOTE_FIELD: quote.to_dict()}))


def complete_fractional_life_cost(spec, effect, response, query):
    actor = effect.get('_choice_actor')
    if not isinstance(spec, FractionalLifePaymentSpec) or actor not in query.active_seats:
        raise SemanticChoiceError('Fractional life payment lost its active payer or price origin')
    try:
        quote = FractionalLifePaymentQuote.from_dict(effect[FRACTIONAL_LIFE_QUOTE_FIELD])
        if response.get('pay', False) is True:
            validate_fractional_payment_quote(quote, query.player_life(actor))
        resolved = FixedEffectPaymentSpec.from_dict(fractional_payment_record(quote.payment))
    except (KeyError, TypeError, ValueError) as exc:raise SemanticChoiceError(str(exc)) from exc
    return complete_fixed_payment_cost(resolved, effect, response, query)
