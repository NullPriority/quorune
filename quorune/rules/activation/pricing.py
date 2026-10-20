from __future__ import annotations

"""Shared read-only ordinary and alternative activation mana prices."""

from ...activation_cost_modifiers import (
    ActivationManaReduction, activation_cost_prices, base_activation_requirements,
)
from ...activation_x import chosen_activation_x
from ...replacement.immutable import FrozenMap
from .cost_modifiers import current_activation_cost_modifiers


LOWEST_COST = 'Lowest legal cost'
MINIMUM_COST = 'Keep one mana if allowed'
_ORDER_CHOICES = {LOWEST_COST: 'minimum_first', MINIMUM_COST: 'unrestricted_first'}


def ordinary_activation_prices(host, seat, source, ability, response=None):
    value = 0 if response is None else chosen_activation_x(ability, response)
    base = base_activation_requirements(ability, announced_x=value or 0)
    reduction = ability.generic_reduction_per_legendary_creature * max(
        0, host._legendary_creatures_controlled(seat))
    modifiers = current_activation_cost_modifiers(host, seat, source, ability)
    prices = activation_cost_prices(base, modifiers,
        own_reductions=(ActivationManaReduction(FrozenMap({'GENERIC': reduction})),))
    return prices, value, modifiers


def select_ordinary_activation_price(host, seat, source, ability, response=None):
    prices, value, modifiers = ordinary_activation_prices(host, seat, source, ability, response)
    requested = (response or {}).get('cost_reduction_order', LOWEST_COST)
    if not modifiers:
        return prices[0], value, modifiers
    if type(requested) is not str or requested not in _ORDER_CHOICES:
        raise ValueError('Select a legal activation mana payment preference')
    order = _ORDER_CHOICES[requested]
    price = next((p for p in prices if p.order == order), prices[0])
    return price, value, modifiers


def activation_price_details(host, seat, source, ability, response):
    price, _value, modifiers = select_ordinary_activation_price(host, seat, source, ability, response)
    if not modifiers:
        return {}
    return {'static_activation_price': {'order': response.get('cost_reduction_order', LOWEST_COST),
        'requirements': dict(price.requirements)}}


def activation_price_choice_schema(host, seat, source, ability):
    prices, _value, modifiers = ordinary_activation_prices(host, seat, source, ability)
    has_minimum = any(spec.minimum_mana for spec in modifiers)
    has_unrestricted = any(spec.generic_adjustment < 0 and not spec.minimum_mana for spec in modifiers)
    has_unrestricted = has_unrestricted or bool(ability.generic_reduction_per_legendary_creature)
    if not modifiers or not (len(prices) > 1 or ability.announced_x_symbols and has_minimum and has_unrestricted):
        return {}
    return {'cost_reduction_order': {'label': 'Mana payment',
        'legal_values': [LOWEST_COST, MINIMUM_COST], 'default': LOWEST_COST}}


def payable_activation_mana_options(host, seat, source, ability):
    from ...activation_mana_cost import payable_activation_mana_options as owned_options
    modifiers = current_activation_cost_modifiers(host, seat, source, ability)
    return owned_options(host, seat, source, ability, modifiers=modifiers)


def select_activation_mana_option(host, seat, source, ability, requested_id):
    from ...activation_mana_cost import select_activation_mana_option as owned_option
    modifiers = current_activation_cost_modifiers(host, seat, source, ability)
    return owned_option(host, seat, source, ability, requested_id, modifiers=modifiers)
