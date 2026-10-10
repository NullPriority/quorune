from __future__ import annotations

"""Specialize one compiler-recognized Power-up price and usage contract."""

from dataclasses import replace
from ..power_up_model import PowerUpSpec, POWER_UP_UNCOMPILED
from ..activation_usage import ActivationLimit
from ..activation_mana_cost import ActivationManaCostOption, fixed_complex_activation_mana_options
from ..replacement.immutable import FrozenMap


def fixed_power_up_ability(ability):
    if POWER_UP_UNCOMPILED not in ability.uncompiled_costs:
        return ability
    unresolved=tuple(cost for cost in ability.uncompiled_costs if cost!=POWER_UP_UNCOMPILED)
    if unresolved or ability.mana_ability or ability.zones!=('battlefield',):
        return ability
    options=fixed_complex_activation_mana_options(ability.mana,ability.complex_symbols) if ability.complex_symbols else (ActivationManaCostOption('power-up-base',FrozenMap(ability.mana)),)
    if not options:
        return ability
    return replace(ability,uncompiled_costs=unresolved,activation_limit=ActivationLimit.POWER_UP_ONCE,
        power_up=PowerUpSpec(),complex_symbols=(),mana_cost_options=options)
