from __future__ import annotations

"""Compile the exact energy-X creature sacrifice and self-return activation."""

from dataclasses import replace

from ..rules.announced_energy_reanimation import (
    declared_energy_reanimation_target_schema,
    energy_reanimation_sacrifice_choice,
    energy_reanimation_text_matches,
)
from ..replacement.immutable import FrozenMap


def specialize_energy_reanimation_price(ability):
    if (ability.announced_energy_reanimation or ability.complex_symbols != ('E',)
            or not energy_reanimation_text_matches(ability.cost_text, ability.effect_text)):
        return ability
    try:
        return replace(
            ability,
            announced_energy_reanimation=True,
            choices=(energy_reanimation_sacrifice_choice(),),
            complex_symbols=(),
            uncompiled_costs=(),
            target_schema=FrozenMap(declared_energy_reanimation_target_schema()),
        )
    except ValueError:
        return ability
