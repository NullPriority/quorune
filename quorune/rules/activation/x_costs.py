from __future__ import annotations

"""Read-only announced-X pricing through the canonical activation cost owner."""

from ...mana_restrictions import ability_mana_spend_context
from .pricing import select_ordinary_activation_price


def activation_requirements(host, seat, source, ability, response=None):
    price, value, _modifiers = select_ordinary_activation_price(host, seat, source, ability, response)
    return dict(price.requirements), value


def validate_activation_x_payment(host, seat, source, ability, requirements):
    if not host._cost_is_affordable(seat, requirements,
            exclude_sources={source.object_id} if ability.tap_source else set(),
            spend_context=ability_mana_spend_context(str(host._effective_card_data(source).get('type_line') or ''))):
        raise ValueError('Insufficient mana for the announced activation X')
