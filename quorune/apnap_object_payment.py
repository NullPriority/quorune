"""Closed timing for an APNAP object payment with a fixed life-loss result."""

from collections.abc import Mapping


APNAP_OBJECT_PAYMENT_CAPABILITY = 'choice.apnap.optional_object_payment_or_life_loss'
DECLINED_LIFE_LOSS = 'declined_life_loss'


def apnap_object_payment_dependencies(effect):
    if not isinstance(effect, Mapping) or DECLINED_LIFE_LOSS not in effect:
        return ()
    amount = effect[DECLINED_LIFE_LOSS]
    if (effect.get('op') != 'choose_cards_apnap'
            or effect.get('players') not in ('all', 'opponents')
            or (effect.get('zone'), effect.get('then'), effect.get('hidden', False))
            not in (('hand', 'discard', True), ('battlefield', 'sacrifice', False))
            or type(effect.get('hidden', False)) is not bool
            or type(effect.get('count')) is not int or effect['count'] != 1
            or type(amount) is not int or amount <= 0 or not isinstance(effect.get('predicate'), Mapping)
            or {'target', 'exclude_ref', 'fallback_effects', 'require_full_count'} & set(effect)):
        raise ValueError('APNAP optional payment requires one closed object choice and fixed life loss')
    return (APNAP_OBJECT_PAYMENT_CAPABILITY,)
