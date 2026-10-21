from __future__ import annotations

from typing import Any

from ..abilities import ActivatedAbility, ActivationLimit
from ..activation_usage import ACTIVATION_ONCE_PER_TURN_CAPABILITY
from ..power_up_model import POWER_UP_CAPABILITY
from ..boast import BOAST_CAPABILITY
from ..activation_condition_model import (
    ACTIVATION_PHASE_CONDITION_CAPABILITY,
    ACTIVATION_PUBLIC_QUERY_CAPABILITY,
    CLASS_LEVEL_ACTIVATION_CAPABILITY,
    ActivationConditionKind,
)


def activated_ability_cost_capabilities(
    ability: ActivatedAbility,
) -> tuple[str, ...]:
    """Return the reviewed capability owners for one typed activation cost."""

    additional: list[str] = []
    if ability.builtin_semantic_key == 'builtin:equip' and (
        ability.life_payment or ability.energy_payment or ability.choices
        or ability.source_counter_removal_cost is not None
    ):
        from ..rules.attachment_actions import FIXED_NONMANA_EQUIP_CAPABILITY
        additional.append(FIXED_NONMANA_EQUIP_CAPABILITY)
    if ability.boast or any(c.kind is ActivationConditionKind.SOURCE_ATTACKED_THIS_TURN for c in ability.activation_conditions):additional.append(BOAST_CAPABILITY)
    from ..rules.selected_counter_costs import SELECTED_PLUS_COUNTER_COST_KIND, SELECTED_COUNTER_COST_CAPABILITY
    if any(choice.kind == SELECTED_PLUS_COUNTER_COST_KIND for choice in ability.choices):
        additional.append(SELECTED_COUNTER_COST_CAPABILITY)
    if ability.announced_x_symbols is not None:
        from ..activation_x import ANNOUNCED_ACTIVATION_X_CAPABILITY
        additional.append(ANNOUNCED_ACTIVATION_X_CAPABILITY)
    if ability.waterbend is not None:
        from ..waterbend import WATERBEND_CAPABILITY
        additional.append(WATERBEND_CAPABILITY)
    if ability.energy_payment:
        additional.append('activation.energy.fixed_payment')
    if ability.random_discard_payment:
        additional.append('activation.random_discard.fixed')
    if ability.power_up is not None:additional.append(POWER_UP_CAPABILITY)
    if ability.source_counter_removal_cost is not None:
        from ..rules.activation.source_counter_choices import ANY_SOURCE_COUNTER_COST_CAPABILITY
        additional.append(ANY_SOURCE_COUNTER_COST_CAPABILITY if ability.source_counter_removal_cost.schema_version == 2
                          else "activation.source_counter_removal.fixed")
    if ability.loyalty_delta is not None and ability.loyalty_delta > 0:
        additional.append("activation.loyalty.positive_counter_cost")
    if ability.activation_limit is ActivationLimit.EXHAUST_ONCE:
        additional.append("activation.exhaust.once_per_object")
    if ability.activation_limit is ActivationLimit.ONCE_PER_TURN:
        additional.append(ACTIVATION_ONCE_PER_TURN_CAPABILITY)
    condition_kinds = {
        condition.kind for condition in ability.activation_conditions
    }
    if condition_kinds.intersection(
        {
            ActivationConditionKind.CONTROLLERS_UPKEEP,
            ActivationConditionKind.CONTROLLERS_TURN_BEFORE_ATTACKERS,
        }
    ):
        additional.append(ACTIVATION_PHASE_CONDITION_CAPABILITY)
    if ActivationConditionKind.PUBLIC_QUERY_COUNT in condition_kinds:
        additional.append(ACTIVATION_PUBLIC_QUERY_CAPABILITY)
    if ActivationConditionKind.CLASS_LEVEL_EQUALS in condition_kinds:
        additional.append(CLASS_LEVEL_ACTIVATION_CAPABILITY)
    if ActivationConditionKind.CONTROLLER_HAS_CITYS_BLESSING in condition_kinds:
        additional.append('activation.condition.citys_blessing')
    if ability.mana_cost_options:
        additional.append("activation.mana_cost.fixed_complex")
    if not ability.mana_ability and (
        ability.discard_source
        or ability.sacrifice_source
        or ability.exile_source
    ):
        additional.append("activation.source_zone_change.fixed")
    if any(
        choice.fixed_zone_change_cost() is not None
        for choice in ability.choices
    ):
        additional.append("activation.selected_zone_change.fixed")
    if any(
        choice.fixed_tap_cost() is not None for choice in ability.choices
    ):
        additional.append("activation.selected_tap.fixed")
    return tuple(dict.fromkeys(additional))


def activated_ability_cost(ability: ActivatedAbility) -> dict[str, Any]:
    """Serialize the compiler-facing cost facts for one activated ability."""

    result = {
        "text": ability.cost_text,
        "mana": dict(ability.mana),
        "complex_symbols": list(ability.complex_symbols),
        "tap_source": ability.tap_source,
        "untap_source": ability.untap_source,
        "discard_source": ability.discard_source,
        "sacrifice_source": ability.sacrifice_source,
        "exile_source": ability.exile_source,
        "life_payment": ability.life_payment,
        "energy_payment": ability.energy_payment,
        "loyalty_delta": ability.loyalty_delta,
        "source_counter_removal_cost": (
            None
            if ability.source_counter_removal_cost is None
            else ability.source_counter_removal_cost.to_dict()
        ),
        "choices": [choice.compact() for choice in ability.choices],
        "uncompiled_costs": list(ability.uncompiled_costs),
    }
    if ability.announced_x_symbols is not None:result['announced_x_symbols']=ability.announced_x_symbols
    if ability.boast:result['boast']=True
    if ability.waterbend is not None:result['waterbend']=ability.waterbend.to_dict()
    if ability.mana_cost_options:
        result["mana_cost_options"] = [
            option.to_dict() for option in ability.mana_cost_options
        ]
    if ability.activation_limit is not None:
        result["activation_limit"] = ability.activation_limit.value
    if ability.power_up is not None:result['power_up']=ability.power_up.to_dict()
    if ability.random_discard_payment:result['random_discard_payment']=ability.random_discard_payment
    if ability.crew_threshold is not None:
        result["crew"] = ability.crew_threshold
    return result


__all__ = [
    "activated_ability_cost",
    "activated_ability_cost_capabilities",
]
