from __future__ import annotations

"""Closed ordinary activation X and fixed numeric-result projection."""

from dataclasses import replace
import re

from ..activation_x import activation_x_symbols
from .declared_effect_amounts import declared_effect_amount_template


def announced_x_activation_cost(ability):
    if ability.announced_x_symbols is not None:
        return ability
    symbols = activation_x_symbols(ability.cost_text)
    if (symbols is None or ability.zones != ('battlefield',) or ability.mana_ability
            or set(ability.complex_symbols) != {'X'} or ability.uncompiled_costs
            or ability.choices or ability.mana_cost_options or ability.waterbend is not None
            or re.search(r"\bX (?:is|can't|cannot)\b|\bwhere X\b|\bspend only\b", ability.effect_text, re.I)):
        return ability
    return replace(ability, complex_symbols=(), announced_x_symbols=symbols)


def announced_x_activation_effect(ability, *, source_name, compile_fixed, text):
    if ability.announced_x_symbols is None:
        return None
    return declared_effect_amount_template(text, source_name=source_name,
        compile_fixed=compile_fixed, cast_x_available=True, forbid_public_x=True)
