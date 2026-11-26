from dataclasses import replace

from ..abilities import ActivatedAbility


def fixed_mill_activation_cost(ability: ActivatedAbility) -> ActivatedAbility:
    remaining = tuple(" ".join(value.casefold().split()) for value in ability.uncompiled_costs)
    if remaining != ("mill a card",) or not ability.tap_source:
        return ability
    try:
        return replace(ability, uncompiled_costs=(), mill_library_cost=1, mana_ability=False)
    except ValueError:
        return ability
