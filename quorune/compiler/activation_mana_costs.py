from __future__ import annotations

"""Compile closed fixed complex mana symbols on activated abilities."""

from dataclasses import replace

from ..abilities import ActivatedAbility
from ..activation_mana_cost import fixed_complex_activation_mana_options


def fixed_complex_activation_mana_cost(
    ability: ActivatedAbility,
) -> ActivatedAbility:
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
