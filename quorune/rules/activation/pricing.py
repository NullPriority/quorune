from __future__ import annotations

"""Shared read-only ordinary and alternative activation mana prices."""

from ...activation_cost_modifiers import (
    ActivationManaReduction, activation_cost_prices, base_activation_requirements,
    ActivationCostModifierSource,
)
from ...activation_x import chosen_activation_x
from ...replacement.immutable import FrozenMap
from .cost_modifiers import current_activation_cost_modifiers
from .targeted_costs import activation_modifier_witnesses


LOWEST_COST = 'Lowest legal cost'
MINIMUM_COST = 'Keep one mana if allowed'
_ORDER_CHOICES = {LOWEST_COST: 'minimum_first', MINIMUM_COST: 'unrestricted_first'}


def _ordinary_prices(host, seat, source, ability, modifiers, response=None):
    value = 0 if response is None else chosen_activation_x(ability, response)
    base = base_activation_requirements(ability, announced_x=value or 0)
    coefficient = ability.generic_reduction_per_legendary_creature
    reduction = coefficient * max(0, host._legendary_creatures_controlled(seat)) if coefficient else 0
    prices = activation_cost_prices(base, modifiers,
        own_reductions=(ActivationManaReduction(FrozenMap({'GENERIC': reduction})),))
    return prices, value, modifiers


def ordinary_activation_prices(host, seat, source, ability, response=None, *, targets=None):
    modifiers = current_activation_cost_modifiers(host, seat, source, ability)
    variants = (_ordinary_prices(host, seat, source, ability, witness.modifiers, response)
        for witness in activation_modifier_witnesses(host, seat, source, ability, modifiers,
            targets=targets, response=response))
    return min(variants, key=lambda variant: min(price.requirements['GENERIC'] for price in variant[0]))


def select_ordinary_activation_price(host, seat, source, ability, response=None, *, targets=None):
    prices, value, modifiers = ordinary_activation_prices(host, seat, source, ability, response, targets=targets)
    requested = (response or {}).get('cost_reduction_order', LOWEST_COST)
    if not modifiers:
        return prices[0], value, modifiers
    if type(requested) is not str or requested not in _ORDER_CHOICES:
        raise ValueError('Select a legal activation mana payment preference')
    order = _ORDER_CHOICES[requested]
    price = next((p for p in prices if p.order == order), prices[0])
    return price, value, modifiers


def activation_price_details(host, seat, source, ability, response, *, targets=None):
    price, _value, modifiers = select_ordinary_activation_price(host, seat, source, ability, response, targets=targets)
    if not modifiers:
        return {}
    return {'static_activation_price': {'order': response.get('cost_reduction_order', LOWEST_COST),
        'requirements': dict(price.requirements)}}


def activation_price_choice_schema(host, seat, source, ability):
    raw = current_activation_cost_modifiers(host, seat, source, ability)
    for witness in activation_modifier_witnesses(host, seat, source, ability, raw):
        prices, _value, modifiers = _ordinary_prices(host, seat, source, ability, witness.modifiers)
        has_minimum = any(spec.minimum_mana for spec in modifiers)
        has_unrestricted = any(spec.generic_adjustment < 0 and not spec.minimum_mana for spec in modifiers)
        has_unrestricted = has_unrestricted or bool(ability.generic_reduction_per_legendary_creature)
        if modifiers and (len(prices) > 1 or ability.announced_x_symbols and has_minimum and has_unrestricted):
            return {'cost_reduction_order': {'label': 'Mana payment',
                'legal_values': [LOWEST_COST, MINIMUM_COST], 'default': LOWEST_COST}}
    return {}


def payable_activation_mana_options(host, seat, source, ability, *, targets=None):
    from ...activation_mana_cost import payable_activation_mana_options as owned_options
    raw = current_activation_cost_modifiers(host, seat, source, ability)
    result = {}
    for witness in activation_modifier_witnesses(host, seat, source, ability, raw, targets=targets):
        for option in owned_options(host, seat, source, ability, modifiers=witness.modifiers):
            result.setdefault(option.option_id, option)
    return tuple(result.values())


def select_activation_mana_option(host, seat, source, ability, requested_id, *, targets=None):
    from ...activation_mana_cost import select_activation_mana_option as owned_option
    raw = current_activation_cost_modifiers(host, seat, source, ability)
    if targets is None and any(isinstance(value, ActivationCostModifierSource) for value in raw):
        raise ValueError('Select activation targets before a target-dependent mana price')
    witness, = activation_modifier_witnesses(host, seat, source, ability, raw, targets=targets or ())
    return owned_option(host, seat, source, ability, requested_id, modifiers=witness.modifiers)


def _ordinary_witness_price_is_payable(host, seat, source, ability, requirements):
    if ability.waterbend is not None:
        from ..waterbend_payments import activation_waterbend_plan
        return activation_waterbend_plan(host, seat, source, ability, requirements, {}, hint=True) is not None
    from ...mana_restrictions import ability_mana_spend_context
    return host._cost_is_affordable(seat, requirements,
        exclude_sources={source.object_id} if ability.tap_source else set(),
        spend_context=ability_mana_spend_context(str(host._effective_card_data(source).get('type_line') or '')))


def _payable_witness_prices(host, seat, source, ability, witness):
    if ability.mana_cost_options:
        from ...activation_mana_cost import payable_activation_mana_options as owned_options
        return tuple({'requirements': dict(option.requirements), 'cost_option': option.option_id,
            'life_payment': option.life_payment, 'snow_payment': option.snow_payment}
            for option in owned_options(host, seat, source, ability, modifiers=witness.modifiers))
    prices, _value, _modifiers = _ordinary_prices(host, seat, source, ability, witness.modifiers)
    return tuple({'requirements': dict(price.requirements), 'order': price.order}
        for price in prices if _ordinary_witness_price_is_payable(host, seat, source, ability, price.requirements))


def activation_target_price_preview(host, seat, source, ability, public_schema):
    if public_schema is None:
        return None
    raw = current_activation_cost_modifiers(host, seat, source, ability)
    if not any(isinstance(value, ActivationCostModifierSource) for value in raw):
        return None
    from ...replacement.immutable import thaw_value
    from ...selection.target_validation import public_target_schema_for_witnesses
    from ...util import stable_json
    witnesses = {}; prices = {}
    for witness in activation_modifier_witnesses(host, seat, source, ability, raw, include_required=True):
        payable = _payable_witness_prices(host, seat, source, ability, witness)
        if not payable:
            continue
        public = {'targets': thaw_value(witness.groups), 'modes': list(witness.modes)}
        witnesses.setdefault(stable_json(public), public)
        for price in payable:
            requirements = price['requirements']
            mana = (f"{{{requirements['GENERIC']}}}" if requirements.get('GENERIC') else '')
            mana += ''.join(('{'+key+'}') * requirements.get(key, 0) for key in 'WUBRGC')
            value = {**public, **price, 'label': 'Pay ' + (mana or '{0}')}
            prices.setdefault(stable_json(value), value)
    if not witnesses:
        raise ValueError('No payable target assignment for this activation')
    return {'target_schema': public_target_schema_for_witnesses(public_schema, tuple(witnesses.values())),
        'target_costs': {'kind': 'generic_adjustment_once_per_source', 'clauses': [], 'prices': list(prices.values())},
        'requirements': min(prices.values(), key=lambda value: sum(value['requirements'].values()))['requirements']}


def activation_priced_offer_payload(host, seat, source, ability, hint, public_schema):
    from .offer_payload import activation_offer_payload
    projection = activation_target_price_preview(host, seat, source, ability, public_schema)
    if projection is None:
        if not ability.mana_cost_options and any(getattr(value, 'schema_version', None) == 3
                for value in current_activation_cost_modifiers(host, seat, source, ability)):
            price, _value, _modifiers = select_ordinary_activation_price(host, seat, source, ability)
            hint = {**hint, 'm': {key: value for key, value in price.requirements.items() if value}}
        return activation_offer_payload(source, ability, hint), public_schema
    priced_hint = {**hint, 'm': {key: value for key, value in projection['requirements'].items() if value}}
    payload = activation_offer_payload(source, ability, priced_hint)
    payload['target_costs'] = projection['target_costs']
    return payload, projection['target_schema']
