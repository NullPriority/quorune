from __future__ import annotations

"""Typed announced activation X, priced by the existing activation owner."""

import re

ANNOUNCED_ACTIVATION_X_CAPABILITY = 'activation.mana_cost.announced_x'
ANNOUNCED_ACTIVATION_X_SACRIFICE_CAPABILITY = 'activation.mana_cost.announced_x_source_sacrifice'
ANNOUNCED_ACTIVATION_X_FIELD = 'announced_x_symbols'


def activation_x_symbols(cost_text: str, *, allow_source_sacrifice: bool = False) -> int | None:
    pattern = r'(?:\{(?:X|\d+|[WUBRGC])\})+(?:, \{T\})?'
    if allow_source_sacrifice:
        pattern += r'(?:, Sacrifice this (?:artifact|battle|creature|enchantment|land|permanent|planeswalker))?'
    if not re.fullmatch(pattern, cost_text):
        return None
    count = cost_text.count('{X}')
    return count or None


def activation_x_capabilities(*, symbols: int | None, sacrifice_source: bool) -> tuple[str, ...]:
    if symbols is None:
        return ()
    return (ANNOUNCED_ACTIVATION_X_CAPABILITY,) + (
        (ANNOUNCED_ACTIVATION_X_SACRIFICE_CAPABILITY,) if sacrifice_source else ())


def validate_activation_x_ability(ability) -> None:
    symbols = ability.announced_x_symbols
    if symbols is None:
        if '{X}' in ability.cost_text.upper() and 'X' not in ability.complex_symbols:
            raise ValueError('An X activation cost requires its retained typed price binding')
        return
    if (type(symbols) is not int or symbols <= 0
            or activation_x_symbols(ability.cost_text, allow_source_sacrifice=True) != symbols
            or ability.zones != ('battlefield',) or ability.mana_ability
            or ability.complex_symbols or ability.uncompiled_costs
            or ability.choices or ability.mana_cost_options or ability.waterbend is not None
            or ability.discard_source or ability.exile_source
            or ability.untap_source or ability.life_payment or ability.energy_payment or ability.random_discard_payment
            or ability.loyalty_delta is not None or ability.source_counter_removal_cost is not None):
        raise ValueError('Announced activation X requires a closed ordinary mana cost')
    from .util import mana_cost_to_vector
    expected, _ = mana_cost_to_vector(ability.cost_text)
    if any(ability.mana.get(key,0) != expected.get(key,0) for key in set(ability.mana)|set(expected)):
        raise ValueError('Activation X fixed mana must match its canonical printed cost')
    if ability.tap_source != ('{T}' in ability.cost_text):
        raise ValueError('Activation X tap obligation must match its printed cost')
    if ability.sacrifice_source != (', Sacrifice this ' in ability.cost_text):
        raise ValueError('Activation X sacrifice obligation must match its printed cost')


def chosen_activation_x(ability, response) -> int | None:
    if ability.announced_x_symbols is None:
        return None
    value = response.get('x')
    if type(value) is not int or value < 0:
        raise ValueError('Activation X must be an explicit nonnegative integer')
    return value


def bind_activation_target_cardinality(ability, schema, response, *, hint=False, target_limit=None):
    from collections.abc import Mapping
    from .target_cardinality import AnnouncedTargetCardinality, bind_announced_target_schema
    if not isinstance(schema, Mapping) or 'count_parameter' not in schema:
        return schema
    if AnnouncedTargetCardinality.from_dict(schema['count_parameter']).parameter == 'any_number':
        if type(target_limit) is not int or target_limit < 0:
            raise ValueError('Any-number targets require the authoritative object bound')
        return bind_announced_target_schema(schema, target_limit)
    if ability.announced_x_symbols is None:
        raise ValueError('Variable target cardinality requires the typed activation-X cost')
    value = 0 if hint else chosen_activation_x(ability, response)
    return bind_announced_target_schema(schema, value)
