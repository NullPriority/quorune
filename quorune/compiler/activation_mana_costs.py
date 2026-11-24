from __future__ import annotations

"""Compile closed fixed complex mana symbols on activated abilities."""

from dataclasses import replace
from . import regex_working_set as re

from ..abilities import ActivatedAbility
from ..activation_mana_cost import ActivationManaCostOption, fixed_complex_activation_mana_options
from ..fixed_mana_abilities import fixed_mana_modes_from_effect, supports_hybrid_filter_price
from ..replacement.immutable import FrozenMap
from ..util import mana_cost_to_vector


def fixed_alternative_activation_mana_cost(ability: ActivatedAbility) -> ActivatedAbility:
    """Compile two ordinary-mana arms with a shared tap, or printed Equip."""
    if (ability.zones != ('battlefield',) or ability.mana_ability or ability.mana_cost_options
            or ability.untap_source or ability.discard_source or ability.sacrifice_source
            or ability.exile_source or ability.life_payment or ability.energy_payment
            or ability.random_discard_payment or ability.loyalty_delta is not None
            or ability.source_counter_removal_cost is not None or ability.choices):
        return ability
    mana = r'(?:\{(?:0|[1-9][0-9]*|[WUBRGC])\})+'
    equip = ability.builtin_semantic_key == 'builtin:equip' and not ability.tap_source
    tap = '' if equip else r',\s*\{T\}'
    match = re.fullmatch(rf'(?P<first>{mana}){tap}\s+or\s+(?P<second>{mana}){tap}',
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
        complex_symbols=(), uncompiled_costs=(), tap_source=not equip)


_PHYREXIAN_SELECTED_PRICE = re.compile(
    r"(?:\{[0-9]+\})?\{(?P<color>[WUBRG])/P\}\{(?P=color)/P\}, "
    r"(?:Sacrifice two other creatures|Discard two cards|"
    r"Exile three creature cards from your graveyard)",
    re.IGNORECASE,
)


def _phyrexian_selected_zone_change_price(ability: ActivatedAbility) -> ActivatedAbility | None:
    """Compose a known object selection with two fixed Phyrexian symbols."""
    if (
        ability.mana_ability
        or ability.zones != ("battlefield",)
        or _PHYREXIAN_SELECTED_PRICE.fullmatch(ability.cost_text.strip()) is None
    ):
        return None
    from .activated_zone_change_costs import fixed_activated_zone_change_cost

    composed = fixed_activated_zone_change_cost(ability)
    if (
        composed.uncompiled_costs
        or len(composed.choices) != 1
        or composed.choices[0].fixed_zone_change_cost() is None
    ):
        return None
    return composed


def fixed_complex_activation_mana_cost(
    ability: ActivatedAbility,
) -> ActivatedAbility:
    ability = fixed_alternative_activation_mana_cost(ability)
    if not ability.complex_symbols or ability.generic_reduction_per_legendary_creature:
        return ability
    if ability.uncompiled_costs or ability.choices:
        composed = _phyrexian_selected_zone_change_price(ability)
        if composed is None:
            return ability
        ability = composed
    options = fixed_complex_activation_mana_options(
        ability.mana,
        ability.complex_symbols,
    )
    if not options:
        return ability
    if ability.mana_ability:
        modes = fixed_mana_modes_from_effect(ability.effect_text)
        if modes is None or not supports_hybrid_filter_price(ability, options, modes):
            return ability
        return replace(ability, complex_symbols=(), mana_cost_options=options, fixed_mana_outputs=modes)
    return replace(
        ability,
        complex_symbols=(),
        mana_cost_options=options,
    )


__all__ = ["fixed_complex_activation_mana_cost"]
