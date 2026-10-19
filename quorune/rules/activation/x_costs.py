from __future__ import annotations

"""Read-only announced-X pricing through the canonical activation cost owner."""

from ...abilities import reduced_requirements
from ...activation_x import chosen_activation_x
from ...mana_restrictions import ability_mana_spend_context


def activation_requirements(host, seat, source, ability, response=None):
    value = 0 if response is None else chosen_activation_x(ability, response)
    return reduced_requirements(ability, announced_x=value or 0,
        legendary_creatures=host._legendary_creatures_controlled(seat)), value


def validate_activation_x_payment(host, seat, source, ability, requirements):
    if not host._cost_is_affordable(seat, requirements,
            exclude_sources={source.object_id} if ability.tap_source else set(),
            spend_context=ability_mana_spend_context(str(host._effective_card_data(source).get('type_line') or ''))):
        raise ValueError('Insufficient mana for the announced activation X')
