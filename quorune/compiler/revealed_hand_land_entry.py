"""Compiler bridge for the paid revealed hand-source land movement family."""

from ..rules.revealed_hand_zone_move import MECHANIC, SPEC_VERSION, revealed_hand_entry_node_capabilities
from ..self_zone_move_followups import compiled_self_entry
from .dependency_gate import DependencyGate, explicit_capabilities_gate


def compiled_activated_self_zone_move(spec):
    if spec.schema_version != SPEC_VERSION:
        return compiled_self_entry(spec)
    return "activated-revealed-hand-land-entry-v1", (spec.effect(),), ("self-zone-move", MECHANIC)


def revealed_hand_entry_activation_gate(**scope):
    if MECHANIC not in scope["mechanics"]:
        return None
    required = revealed_hand_entry_node_capabilities(
        effects=scope["effects"], target_schema=scope["target_schema"], mechanic_ids=scope["mechanics"],
    )
    if not required or scope["active_zone"] != "hand":
        return DependencyGate(blockers=(f"mechanic:{MECHANIC}",))
    return explicit_capabilities_gate(
        required, capability_registry=scope["capability_registry"], capability_profile=scope["capability_profile"],
    )


def activated_result_dependency_gate(**scope):
    from .activation_paid_sacrifice_results import activation_result_dependency_gate

    return revealed_hand_entry_activation_gate(**scope) or activation_result_dependency_gate(**scope)
