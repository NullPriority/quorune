from __future__ import annotations

"""Closed ordinary activation X and fixed numeric-result projection."""

from dataclasses import replace
from . import regex_working_set as re

from ..activation_x import (
    CONTROLLED_LAND_RETURN_X_COST, activation_x_symbols, activation_x_origin_is_supported,
    has_controlled_land_return_x_price, validate_activation_x_selected_cost,
)
from ..activation_x_discard import activation_x_discard_count, validate_activation_x_discard
from ..rules.announced_subtype_sacrifice import (
    ANNOUNCED_SUBTYPE_SACRIFICE_KIND, ANNOUNCED_SUBTYPE_STATS_TEXT, announced_subtype_sacrifice_choice,
    announced_subtype_sacrifice_predicate, printed_announced_subtype_price,
)
from .declared_effect_amounts import declared_effect_amount_template


def _announced_subtype_cost(ability):
    if (" ".join(ability.effect_text.split()).casefold() != ANNOUNCED_SUBTYPE_STATS_TEXT
            or len(ability.uncompiled_costs) != 1 or ability.choices):
        return None
    printed = printed_announced_subtype_price(ability.cost_text)
    if printed is None:
        return None
    from ..activation_cost_choice import CostChoice
    from ..replacement.immutable import FrozenMap

    _mana, subtype = printed
    choice = CostChoice(
        ANNOUNCED_SUBTYPE_SACRIFICE_KIND, count=0,
        predicate=FrozenMap(announced_subtype_sacrifice_predicate(subtype).to_dict()),
    )
    try:
        return replace(ability, choices=(choice,), uncompiled_costs=())
    except ValueError:
        return None


def announced_x_activation_cost(ability):
    if subtype_choice := _announced_subtype_cost(ability):
        return subtype_choice
    if "Reveal X " in ability.cost_text:
        from .variable_colored_reveal_price import specialize_variable_colored_reveal_price

        return specialize_variable_colored_reveal_price(ability)
    if ability.announced_x_symbols is not None:
        return ability
    if ability.cost_text == CONTROLLED_LAND_RETURN_X_COST:
        from .activated_zone_change_costs import fixed_activated_zone_change_cost

        ability = fixed_activated_zone_change_cost(ability)
    if ability.cost_text.startswith("Pay X {E},"):
        from .announced_energy_reanimation import specialize_energy_reanimation_price
        return specialize_energy_reanimation_price(ability)
    symbols = activation_x_symbols(ability.cost_text, allow_source_sacrifice=True, allow_fixed_discard=True,
        allow_source_discard=ability.discard_source is True,
        allow_controlled_land_return=has_controlled_land_return_x_price(ability))
    if symbols is not None and activation_x_discard_count(ability.cost_text) is not None:
        from .activated_zone_change_costs import fixed_activated_zone_change_cost
        printed_cost = ability.cost_text
        if activation_x_discard_count(printed_cost) == 1:
            ability = replace(ability, cost_text=printed_cost.rpartition(', Discard ')[0]+', Discard a card')
        ability = replace(fixed_activated_zone_change_cost(ability), cost_text=printed_cost)
    try:
        validate_activation_x_selected_cost(ability)
    except ValueError:
        return ability
    from ..rules.announced_ability_copies import announced_ability_copy_body
    positive = ability.cost_text == "{X}{X}, {T}" and announced_ability_copy_body(ability.effect_text)
    if (symbols is None or not activation_x_origin_is_supported(ability) or ability.mana_ability
            or set(ability.complex_symbols) != {'X'} or ability.uncompiled_costs
            or ability.mana_cost_options or ability.waterbend is not None
            or not positive and re.search(r"\bX (?:is|can't|cannot)\b|\bwhere X\b|\bspend only\b", ability.effect_text, re.I)):
        return ability
    return replace(ability, complex_symbols=(), announced_x_symbols=symbols, announced_x_minimum=int(positive))


def announced_x_activation_effect(ability, *, source_name, compile_fixed, text):
    if not ability.chooses_activation_x:
        return None
    if announced_subtype_sacrifice_choice(ability) is not None:
        from .temporary_target_interactions import temporary_target_interaction_effect_template
        interaction = temporary_target_interaction_effect_template(text)
        return interaction.compiled() if interaction is not None else None
    if ability.announced_x_minimum:
        from ..rules.announced_ability_copies import announced_ability_copy_body, copy_effect, target_schema
        if announced_ability_copy_body(text):
            return "announced-controlled-ability-copies-v1", (copy_effect(),), target_schema(), ("cr-115-targets",)
        return None
    if ability.variable_colored_reveal_price is not None:
        from .counter_templates import targeted_controller_payment_template

        payment = targeted_controller_payment_template(text, cast_x_available=True, source_name=source_name)
        if payment is not None and payment.payment.uses_cast_x:
            return payment.compiled()
    from .creature_subtype_set_templates import paid_controller_subtype_set_template

    if subtype_set := paid_controller_subtype_set_template(ability, text):
        return subtype_set
    from .announced_mana_value_target import announced_activation_mana_value_target_template

    if target := announced_activation_mana_value_target_template(ability, text):
        return target
    from .variable_library_selection_templates import variable_up_to_two_hand_selection_template

    selection = variable_up_to_two_hand_selection_template(
        text, source_name=source_name, cast_x_available=True, forbid_public_x=True,
    )
    if selection is not None:
        return selection
    return declared_effect_amount_template(text, source_name=source_name,
        compile_fixed=compile_fixed, cast_x_available=True, forbid_public_x=True)
