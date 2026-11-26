from __future__ import annotations

from ..rules.void_counter_play import (
    CAPABILITY, CHOICE_FIELD, DEPENDENCIES, MECHANIC, choice_effect,
    is_choice_effect, is_closed_price,
)
from .activated_costs import activated_ability_cost
from .dependency_gate import DependencyGate, explicit_capabilities_gate


_BODY = (
    "choose an exiled card an opponent owns with a void counter on it. "
    "you may play it this turn without paying its mana cost."
)


def void_counter_play_activation_result(ability, text, *, public_timing):
    if (
        text.casefold() != _BODY or ability.mana_ability or ability.zones != ("battlefield",)
        or not is_closed_price(activated_ability_cost(ability, public_timing=public_timing))
    ):
        return None
    return ("opponent-void-counter-card-play-v1", (choice_effect(),), None, (MECHANIC,))


def void_counter_play_activation_gate(
    *, effects, target_schema, mechanics, active_zone, cost_schema, capability_registry, capability_profile,
):
    if not any(CHOICE_FIELD in effect for effect in effects):
        return None
    if (
        len(effects) != 1 or not is_choice_effect(effects[0]) or target_schema is not None
        or mechanics != (MECHANIC,) or active_zone != "battlefield" or not is_closed_price(cost_schema)
    ):
        return DependencyGate(blockers=(f"capability:{CAPABILITY}",))
    return explicit_capabilities_gate(
        DEPENDENCIES, capability_registry=capability_registry, capability_profile=capability_profile,
    )
