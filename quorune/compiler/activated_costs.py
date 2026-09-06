from __future__ import annotations

from typing import Any

from ..abilities import ActivatedAbility, ActivationLimit
from ..activation_condition_model import (
    ACTIVATION_PHASE_CONDITION_CAPABILITY,
    ACTIVATION_PUBLIC_QUERY_CAPABILITY,
    ActivationConditionKind,
)


def activated_ability_cost_capabilities(
    ability: ActivatedAbility,
) -> tuple[str, ...]:
    """Return the reviewed capability owners for one typed activation cost."""

    additional: list[str] = []
    if ability.loyalty_delta is not None and ability.loyalty_delta > 0:
        additional.append("activation.loyalty.positive_counter_cost")
    if ability.activation_limit is ActivationLimit.EXHAUST_ONCE:
        additional.append("activation.exhaust.once_per_object")
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
    if ability.mana_cost_options:
        additional.append("activation.mana_cost.fixed_complex")
    if not ability.mana_ability and (
        ability.discard_source
        or ability.sacrifice_source
        or ability.exile_source
    ):
        additional.append("activation.source_zone_change.fixed")
    if not ability.mana_ability and any(
        choice.fixed_zone_change_cost() is not None
        for choice in ability.choices
    ):
        additional.append("activation.selected_zone_change.fixed")
    if not ability.mana_ability and any(
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
        "choices": [choice.compact() for choice in ability.choices],
        "uncompiled_costs": list(ability.uncompiled_costs),
    }
    if ability.mana_cost_options:
        result["mana_cost_options"] = [
            option.to_dict() for option in ability.mana_cost_options
        ]
    if ability.activation_limit is not None:
        result["activation_limit"] = ability.activation_limit.value
    if ability.crew_threshold is not None:
        result["crew"] = ability.crew_threshold
    return result


__all__ = [
    "activated_ability_cost",
    "activated_ability_cost_capabilities",
]
