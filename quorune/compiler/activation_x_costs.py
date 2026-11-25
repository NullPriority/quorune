from __future__ import annotations

"""Closed ordinary activation X and fixed numeric-result projection."""

from dataclasses import replace
from . import regex_working_set as re

from ..activation_x import activation_x_symbols, activation_x_origin_is_supported
from ..activation_x_discard import activation_x_discard_count, validate_activation_x_discard
from .declared_effect_amounts import declared_effect_amount_template


def announced_x_activation_cost(ability):
    if "Reveal X " in ability.cost_text:
        from .variable_colored_reveal_price import specialize_variable_colored_reveal_price

        return specialize_variable_colored_reveal_price(ability)
    if ability.announced_x_symbols is not None:
        return ability
    symbols = activation_x_symbols(ability.cost_text, allow_source_sacrifice=True, allow_fixed_discard=True,
        allow_source_discard=ability.discard_source is True)
    if symbols is not None and activation_x_discard_count(ability.cost_text) is not None:
        from .activated_zone_change_costs import fixed_activated_zone_change_cost
        printed_cost = ability.cost_text
        if activation_x_discard_count(printed_cost) == 1:
            ability = replace(ability, cost_text=printed_cost.rpartition(', Discard ')[0]+', Discard a card')
        ability = replace(fixed_activated_zone_change_cost(ability), cost_text=printed_cost)
    try:
        validate_activation_x_discard(ability)
    except ValueError:
        return ability
    if (symbols is None or not activation_x_origin_is_supported(ability) or ability.mana_ability
            or set(ability.complex_symbols) != {'X'} or ability.uncompiled_costs
            or ability.mana_cost_options or ability.waterbend is not None
            or re.search(r"\bX (?:is|can't|cannot)\b|\bwhere X\b|\bspend only\b", ability.effect_text, re.I)):
        return ability
    return replace(ability, complex_symbols=(), announced_x_symbols=symbols)


def announced_x_activation_effect(ability, *, source_name, compile_fixed, text):
    if not ability.chooses_activation_x:
        return None
    if ability.variable_colored_reveal_price is not None:
        from .counter_templates import targeted_controller_payment_template

        payment = targeted_controller_payment_template(text, cast_x_available=True, source_name=source_name)
        if payment is not None and payment.payment.uses_cast_x:
            return payment.compiled()
    return declared_effect_amount_template(text, source_name=source_name,
        compile_fixed=compile_fixed, cast_x_available=True, forbid_public_x=True)
