from __future__ import annotations

"""Lower the fixed paid d10 result through the public owner-library move."""

from ..rules.die_roll_library_depth import (
    CAPABILITY, FIELD, MECHANIC, die_library_depth_dependencies,
    die_library_depth_effect, die_library_depth_target_schema, has_die_library_depth_price,
)
from .activated_costs import activated_ability_cost
from .dependency_gate import DependencyGate, explicit_capabilities_gate


_PRINTED_RESULT = (
    "roll a d10. put target artifact, creature, or planeswalker into its owner's library "
    "just beneath the top x cards of that library, where x is the result."
)


def die_library_depth_activation(ability, text, *, public_timing):
    if (" ".join(text.replace("’", "'").split()).casefold() != _PRINTED_RESULT
            or public_timing is not None or ability.mana_ability or not ability.sorcery_speed
            or ability.activation_conditions or ability.activation_limit is not None
            or ability.zones != ("battlefield",)
            or not has_die_library_depth_price(activated_ability_cost(ability))):
        return None
    return ("paid-d10-owner-library-depth-v1", (die_library_depth_effect(),),
            die_library_depth_target_schema(), (MECHANIC, "cr-115-targets"))


def die_library_depth_activation_gate(**scope):
    if not any(FIELD in effect for effect in scope["effects"]):
        return None
    required = die_library_depth_dependencies(**{
        key: scope[key] for key in ("effects", "target_schema", "mechanics", "active_zone", "cost_schema")
    })
    if not required:
        return DependencyGate(blockers=(f"capability:{CAPABILITY}",))
    return explicit_capabilities_gate(
        required, capability_registry=scope["capability_registry"], capability_profile=scope["capability_profile"],
    )
