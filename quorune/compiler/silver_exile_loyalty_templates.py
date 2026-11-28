"""Exact revealed pair and public countered-exile loyalty bodies."""
from ..public_library_piles_model import PILE_MECHANIC, PILE_SILVER_MECHANIC
from ..rules.silver_exile_loyalty import (
    SILVER_EXILE_RETURN_MECHANIC, silver_loyalty_dependencies, silver_pair_effect, silver_return_effect,
)
from .activated_costs import activated_ability_cost
from .dependency_gate import explicit_capabilities_gate


def silver_loyalty_activation_result(ability, text, *, public_timing):
    wording = " ".join(text.casefold().split())
    if wording == ("reveal the top two cards of your library. an opponent chooses one of them. "
                   "put that card into your hand and exile the other with a silver counter on it."):
        result = silver_pair_effect()
        mechanics = (PILE_MECHANIC, PILE_SILVER_MECHANIC)
        template = "public-library-pair-with-silver-v1"
    elif wording == "put a card you own with a silver counter on it from exile into your hand.":
        result = silver_return_effect()
        mechanics = (SILVER_EXILE_RETURN_MECHANIC,)
        template = "owned-silver-exile-to-hand-v1"
    else:
        return None
    cost = activated_ability_cost(ability, public_timing=public_timing)
    if (ability.mana_ability or ability.target_schema is not None or ability.zones != ("battlefield",)
            or ability.activation_conditions or not silver_loyalty_dependencies(
                effects=(result,), target_schema=None, active_zone="battlefield", cost_schema=cost)):
        return None
    return template, (result,), None, mechanics


def silver_loyalty_activation_gate(*, effects, target_schema, active_zone, cost_schema,
                                   capability_registry, capability_profile, **unused):
    required = silver_loyalty_dependencies(effects=effects, target_schema=target_schema,
        active_zone=active_zone, cost_schema=cost_schema)
    return explicit_capabilities_gate(required, capability_registry=capability_registry,
        capability_profile=capability_profile) if required else None
