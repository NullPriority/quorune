from __future__ import annotations

"""Seal per-player fractional domains before APNAP choices are collected."""

from collections.abc import Mapping

from ..errors import GameRuleError
from ..fractional_quantity_model import HalfQuantitySpec
from ..third_quantity_model import THIRD_QUANTITY_KIND, ThirdQuantitySpec
from ..alternative_discard_model import alternative_discard_count
from ..rules.any_number_sacrifice import ANY_NUMBER_SACRIFICE_FIELD, any_number_sacrifice_count
from .alternative_discard import decoded_alternative_discard_count


def _fractional_choice_count(value):
    if isinstance(value, Mapping) and value.get("kind") == THIRD_QUANTITY_KIND:
        return ThirdQuantitySpec.from_dict(value)
    return HalfQuantitySpec.from_dict(value)


def validate_apnap_count(effect):
    count = effect.get('count', 1)
    if alternative_discard_count(count):
        decoded_alternative_discard_count(count)
        if effect.get('then')!='discard' or effect.get('zone')!='hand' or effect.get('hidden') is not True:
            raise GameRuleError('Alternative counts require a private discard instruction')
        return
    if any_number_sacrifice_count(count):
        if ANY_NUMBER_SACRIFICE_FIELD not in effect or effect.get("then") != "sacrifice":
            raise GameRuleError("Any-number selection requires its exact sacrifice result")
        return
    if type(count) is int and count > 0:
        return
    try:
        fraction = _fractional_choice_count(count)
    except (TypeError, ValueError) as exc:
        raise GameRuleError('APNAP choice count must be positive or a closed half quantity') from exc
    if isinstance(fraction, ThirdQuantitySpec) and (
        fraction.rounding != "up" or effect.get("players") != "all"
        or effect.get("then") == "discard" and (effect.get("zone") != "hand" or effect.get("hidden") is not True)
        or effect.get("then") == "sacrifice" and (effect.get("zone") != "battlefield" or "hidden" in effect)
    ):
        raise GameRuleError("Third choices require the original all-player private discard or public sacrifice")
    if effect.get('then') not in {'sacrifice', 'discard'} or effect.get('require_full_count'):
        raise GameRuleError('Fractional choices require a represented discard or sacrifice domain')


def seal_fractional_counts(host, state):
    count = state['effect'].get('count', 1)
    if type(count) is int or alternative_discard_count(count) or any_number_sacrifice_count(count):
        return
    fraction = _fractional_choice_count(count)
    domains = {seat: host._choice_options(seat, state['effect']) for seat in state['player_order']}
    state['fractional_domains'] = domains
    state['fractional_counts'] = {seat: fraction.amount(len(refs)) for seat, refs in domains.items()}


def apnap_required_count(state, seat, options):
    count = state['effect'].get('count', 1)
    if alternative_discard_count(count):
        return min(decoded_alternative_discard_count(count).fallback_count,len(options))
    if any_number_sacrifice_count(count):
        return len(options)
    if type(count) is int:
        return min(count, len(options))
    try:
        fraction = _fractional_choice_count(count)
        domains, counts = state['fractional_domains'], state['fractional_counts']
        if (not isinstance(domains, Mapping) or not isinstance(counts, Mapping)
                or set(domains) != set(state['player_order']) or set(counts) != set(domains)
                or list(domains[seat]) != list(options) or type(counts[seat]) is not int
                or counts[seat] != fraction.amount(len(domains[seat]))):
            raise ValueError('Fractional choice domain or sealed count changed')
        return counts[seat]
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
