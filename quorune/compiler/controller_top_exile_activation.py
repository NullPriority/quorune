"""Lower the fixed tap and life-paid top-exile activation."""

from ..rules.controller_top_exile_activation import (
    CONTROLLER_TOP_EXILE_CAPABILITY,
    CONTROLLER_TOP_EXILE_MECHANIC,
    controller_top_exile_activation_dependencies,
    controller_top_exile_effect,
)
from .activated_costs import activated_ability_cost
from .dependency_gate import DependencyGate, explicit_capabilities_gate


def controller_top_exile_activation(ability, text, *, public_timing):
    if (
        " ".join(text.casefold().split()) != "exile the top card of your library."
        or ability.zones != ("battlefield",) or ability.mana_ability or ability.sorcery_speed
        or ability.activation_conditions
    ):
        return None
    effects = (controller_top_exile_effect(),)
    cost = activated_ability_cost(ability, public_timing=public_timing)
    if not controller_top_exile_activation_dependencies(effects, None, "battlefield", cost):
        return None
    return "controller-top-exile-activation-v1", effects, None, (CONTROLLER_TOP_EXILE_MECHANIC,)


def controller_top_exile_activation_gate(**scope):
    if CONTROLLER_TOP_EXILE_MECHANIC not in scope["mechanics"]:
        return None
    required = controller_top_exile_activation_dependencies(
        scope["effects"], scope["target_schema"], scope["active_zone"], scope["cost_schema"],
    )
    if not required:
        return DependencyGate(blockers=(f"capability:{CONTROLLER_TOP_EXILE_CAPABILITY}",))
    return explicit_capabilities_gate(
        required,
        capability_registry=scope["capability_registry"],
        capability_profile=scope["capability_profile"],
    )
