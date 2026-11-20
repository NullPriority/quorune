"""Consume one complete optional nontoken-creature price and fixed token stat result."""
from . import regex_working_set as re
from ..fixed_effect_payment import FixedEffectPaymentSpec, FIXED_EFFECT_PAYMENT_MECHANIC
from ..object_predicate import ObjectQuerySpec
from ..optional_sacrifice_stat_model import ResolutionPaidCreatureStatSpec, OPTIONAL_SACRIFICE_STAT_WRAPPER_VERSION
from ..rules.optional_sacrifice_stats import OPTIONAL_SACRIFICE_STAT_MECHANIC


def optional_sacrifice_stat_binding_result(binding, body, *, compile_fixed):
    from ..replacement.immutable import thaw_value
    if (binding.event.value != 'step.begin' or binding.active_zone != 'battlefield'
            or thaw_value(binding.event_condition) != {'all': [
                {'field': 'player', 'op': 'eq', 'value': '$source.controller'},
                {'field': 'step', 'op': 'eq', 'value': 'end_step'}]}):
        return None
    result = optional_sacrifice_stat_result(body, compile_fixed=compile_fixed)
    return (result, False) if result is not None else None


def optional_sacrifice_stat_result(text, *, compile_fixed):
    match = re.fullmatch(r"You may sacrifice a nontoken creature\. If you do, create X "
        r"(?P<definition>[^.\n]+? creature tokens), where X is the sacrificed creature['’]s (?P<stat>power|toughness)\.",
        ' '.join(text.strip().split()), re.I)
    if match is None:
        return None
    fixed = compile_fixed('Create a ' + match['definition'][:-1] + '.')
    if fixed[0] is None or len(fixed[1]) != 1 or fixed[2] is not None:
        return None
    payment = FixedEffectPaymentSpec('sacrifice', predicate=ObjectQuerySpec(zones=('battlefield',),
        controller='$controller', types_all=('creature',), token=False))
    wrapper = {'op': 'offer_optional_mana_payment', 'schema_version': OPTIONAL_SACRIFICE_STAT_WRAPPER_VERSION,
        'player': '$controller', 'payment': payment.to_dict(), 'result': fixed[1][0],
        'stat': ResolutionPaidCreatureStatSpec(match['stat'].casefold()).to_dict()}
    from ..rules.optional_sacrifice_stats import optional_sacrifice_stat_node_capabilities
    if not optional_sacrifice_stat_node_capabilities(effects=(wrapper,), target_schema=None):
        return None
    return 'optional-sacrifice-creature-stat-result-v1', (wrapper,), None, tuple(dict.fromkeys((
        OPTIONAL_SACRIFICE_STAT_MECHANIC, FIXED_EFFECT_PAYMENT_MECHANIC, *fixed[3])))
