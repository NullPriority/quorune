from __future__ import annotations

"""Compile closed fixed complex mana symbols on activated abilities."""

from dataclasses import replace
import re

from ..abilities import ActivatedAbility
from ..activation_mana_cost import ActivationManaCostOption, fixed_complex_activation_mana_options
from ..replacement.immutable import FrozenMap
from ..util import mana_cost_to_vector


def fixed_alternative_activation_mana_cost(ability: ActivatedAbility) -> ActivatedAbility:
    """Compile two complete ordinary-mana arms with one shared source tap."""
    if (ability.zones != ('battlefield',) or ability.mana_ability or ability.mana_cost_options
            or ability.untap_source or ability.discard_source or ability.sacrifice_source
            or ability.exile_source or ability.life_payment or ability.energy_payment
            or ability.random_discard_payment or ability.loyalty_delta is not None
            or ability.source_counter_removal_cost is not None or ability.choices):
        return ability
    mana = r'(?:\{(?:0|[1-9][0-9]*|[WUBRGC])\})+'
    match = re.fullmatch(rf'(?P<first>{mana}),\s*\{{T\}}\s+or\s+(?P<second>{mana}),\s*\{{T\}}',
        ability.cost_text.strip(), re.I)
    if match is None:
        return ability
    options = []
    for index, text in enumerate((match['first'], match['second'])):
        vector, complex_symbols = mana_cost_to_vector(text.upper())
        if complex_symbols:
            return ability
        options.append(ActivationManaCostOption(f'fixed-alternative:{index + 1}', FrozenMap(vector)))
    if options[0].requirements == options[1].requirements:
        return ability
    return replace(ability, mana=dict(options[0].requirements), mana_cost_options=tuple(options),
        complex_symbols=(), uncompiled_costs=(), tap_source=True)


def fixed_complex_activation_mana_cost(
    ability: ActivatedAbility,
) -> ActivatedAbility:
    ability = fixed_alternative_activation_mana_cost(ability)
    if (
        ability.mana_ability
        or not ability.complex_symbols
        or ability.uncompiled_costs
        or ability.choices
        or ability.generic_reduction_per_legendary_creature
    ):
        return ability
    options = fixed_complex_activation_mana_options(
        ability.mana,
        ability.complex_symbols,
    )
    if not options:
        return ability
    return replace(
        ability,
        complex_symbols=(),
        mana_cost_options=options,
    )


__all__ = ["fixed_complex_activation_mana_cost"]
