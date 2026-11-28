"""Lower the exact untargeted sorcery activation that adds a combat and main."""

from .dependency_gate import DependencyGate, explicit_capabilities_gate
from ..extra_combat_model import CONTROLLED_UNTAP_COMBAT_MECHANIC, ExtraCombatUntap, extra_combat_effect
from ..rules.controlled_creature_main_combat import (
    controlled_main_combat_node_capabilities, ordinary_sorcery_combat_activation,
)


_BODY = (
    "untap all creatures you control. after this main phase, there is an additional "
    "combat phase followed by an additional main phase."
)


def controlled_untap_combat_activation(ability, text, *, public_timing):
    material = " ".join(text.casefold().split()).removesuffix(" activate only as a sorcery.")
    if public_timing is not None or material != _BODY or not ordinary_sorcery_combat_activation(ability):
        return None
    effect = extra_combat_effect(ExtraCombatUntap.CONTROLLED_CREATURES_MAIN_PAIR)
    return f"{CONTROLLED_UNTAP_COMBAT_MECHANIC}-v1", (effect,), None, (CONTROLLED_UNTAP_COMBAT_MECHANIC,)


def controlled_untap_combat_activation_gate(**scope):
    if CONTROLLED_UNTAP_COMBAT_MECHANIC not in scope["mechanics"]:
        return None
    required = controlled_main_combat_node_capabilities(scope["effects"], scope["target_schema"], scope["cost_schema"])
    if scope["active_zone"] != "battlefield" or not required:
        return DependencyGate(blockers=(f"mechanic:{CONTROLLED_UNTAP_COMBAT_MECHANIC}",))
    return explicit_capabilities_gate(
        required, capability_registry=scope["capability_registry"], capability_profile=scope["capability_profile"],
    )
