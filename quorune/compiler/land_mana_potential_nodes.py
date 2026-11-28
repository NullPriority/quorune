from __future__ import annotations

"""Source-spanned lowering for mana based on another land's live potential."""

from ..land_mana_potential import (
    LAND_MANA_POTENTIAL_HANDLER_ID, compile_land_potential_mana,
)
from .activated_costs import activated_ability_cost
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual


def land_potential_mana_node(ability, node_id, line, span, capability_registry, capability_profile, residuals):
    spec = compile_land_potential_mana(ability)
    if spec is None:
        return None
    gate = explicit_capabilities_gate(
        spec.output.capabilities, capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    residual_ids = (append_residual(
        residuals, kind="dependency_contract", text=line, span=span,
        reason="Land mana potential lacks trusted output ownership", blockers=gate.blockers,
    ),) if gate.blockers else ()
    return OracleNode(
        node_id=node_id, kind="mana_ability", text=line, span=span, active_zone="battlefield",
        event="activate", lowerable=True, exact=not gate.blockers,
        template_id="activated-mana-land-potential-v1", cost=activated_ability_cost(ability),
        handlers=({"handler_id": LAND_MANA_POTENTIAL_HANDLER_ID, "schema_version": 1,
                   "event": "activate", "ability": spec.to_dict()},),
        mechanics=(), residual_ids=residual_ids, capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None,
    )
