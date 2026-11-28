"""Bind counted counter wording to one proved controller life-gain occurrence."""
from . import regex_working_set as re
from ..counter_names import normalized_counter_name


def _is_controller_life_gain(binding):
    return (binding.event.value == "life.gained" and binding.variant == "controller_life_gain"
            and binding.event_condition == {"field": "player", "op": "eq", "value": "$source.controller"})


def life_gain_counter_reset_result(text, *, binding):
    from ..rules.life_gain_counter_reset import LIFE_GAIN_COUNTER_RESET_MECHANIC, life_gain_counter_reset_effects

    if not _is_controller_life_gain(binding) or not re.fullmatch(
        r"Put that many ribbon counters on this creature[.] Then if there are "
        r"three or more ribbon counters on this creature, remove those counters and untap it[.]?",
        text.strip(), re.I,
    ):
        return None
    return ("life-gain-counter-threshold-reset-v1", life_gain_counter_reset_effects(),
            None, (LIFE_GAIN_COUNTER_RESET_MECHANIC,)), True


def life_gain_counted_self_growth_result(text, *, binding):
    if not _is_controller_life_gain(binding):
        return None
    if not re.fullmatch(
        r"This creature gets \+X/\+X until end of turn, where X is the amount of life you gained\.?",
        text.strip(), re.I,
    ):
        return None
    return "This creature gets +that much/+that much until end of turn."


def life_gain_counted_counter_result(text, *, binding):
    if not _is_controller_life_gain(binding):
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
    if not _is_controller_life_gain(binding):
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


def capped_life_gain_draw_payment(text, *, binding):
    if not _is_controller_life_gain(binding) or not re.fullmatch(
        r"You may pay \{X\}, where X is less than or equal to the amount of life you gained\. "
        r"If you do, draw X cards\.", text.strip(), re.I,
    ):
        return None
    from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC
    from ..rules.life_gain_counted_results import capped_life_gain_draw_effect

    return ("life-gain-capped-mana-draw-v1", (capped_life_gain_draw_effect(),),
            None, (FIXED_EFFECT_PAYMENT_MECHANIC,)), False


def life_gain_life_draw_payment(text, *, binding):
    if not _is_controller_life_gain(binding) or not re.fullmatch(
        r"You may pay that much life\. If you do, draw that many cards\.", text.strip(), re.I,
    ):
        return None
    from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC
    from ..rules.life_gain_counted_results import life_gain_life_draw_effect

    return ("life-gain-life-payment-and-draw-v1", (life_gain_life_draw_effect(),),
            None, (FIXED_EFFECT_PAYMENT_MECHANIC,)), False
