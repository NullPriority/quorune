"""Bind counted counter wording to one proved controller life-gain occurrence."""
from . import regex_working_set as re
from ..counter_names import normalized_counter_name


def life_gain_counted_counter_result(text, *, binding):
    if (binding.event.value != 'life.gained' or binding.variant != 'controller_life_gain'
            or binding.event_condition != {'field': 'player', 'op': 'eq', 'value': '$source.controller'}):
        return None
    match = re.fullmatch(r'Put (?:a|an|one) (?P<counter>[A-Za-z0-9+/-]+) counter on '
        r'(?P<receiver>target creature|this creature|enchanted creature) for each 1 life you gained\.',
        text.strip(), re.I)
    if match is None:
        return None
    try:
        counter = normalized_counter_name(match['counter'])
    except (TypeError, ValueError):
        return None
    return f"Put that many {counter} counters on {match['receiver'].casefold()}."


def life_gain_counted_counter_payment(text, *, binding, compile_result):
    if (binding.event.value != 'life.gained' or binding.variant != 'controller_life_gain'
            or binding.event_condition != {'field': 'player', 'op': 'eq', 'value': '$source.controller'}):
        return None
    from .optional_payment_templates import fixed_optional_mana_payment_template
    from ..fixed_effect_payment import FixedEffectPaymentSpec
    from ..replacement.immutable import FrozenMap
    from ..rules.life_gain_counted_results import LIFE_GAIN_COUNTER_RESULT_FIELD, life_gain_counter_payment_node_capabilities
    current = False
    def child(body):
        nonlocal current
        if life_gain_counted_counter_result(body, binding=binding) is None:
            return None, (), None, ()
        compiled, current = compile_result(body)
        return compiled
    old = fixed_optional_mana_payment_template(text, compile_effect=child)
    if old is None:
        return None
    original = old.effects[0]
    payment = FixedEffectPaymentSpec('mana', requirements=FrozenMap(original['cost']))
    wrapper = {**original, 'schema_version': 2, 'payment': payment.to_dict(), LIFE_GAIN_COUNTER_RESULT_FIELD: 1}
    if not life_gain_counter_payment_node_capabilities(effects=(wrapper,), target_schema=old.target_schema):
        return None
    return ('life-gain-counted-counter-payment-v1', (wrapper,), old.target_schema, old.mechanics), current
