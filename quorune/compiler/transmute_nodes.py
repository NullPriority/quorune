from __future__ import annotations

from ..semantic_runtime.activated_abilities import activated_ability_catalog_descriptor
from ..transmute import TRANSMUTE_CAPABILITY, fixed_transmute_ability, transmute_search_effect
from .activated_costs import activated_ability_cost
from .dependency_gate import explicit_capability_gate
from .ir_model import OracleNode, append_residual


def fixed_transmute_keyword_node(*, node_id, line, material_line, span, mechanics,
    capability_registry, capability_profile, residuals):
    if mechanics != ('transmute',):
        return None
    ability = fixed_transmute_ability(material_line=material_line, oracle_line=line, line_index=span.line - 1)
    if ability is None:
        return None
    gate = explicit_capability_gate(TRANSMUTE_CAPABILITY,
        capability_registry=capability_registry, capability_profile=capability_profile)
    residual_ids = (append_residual(residuals, kind='dependency_contract', text=line, span=span,
        reason='Transmute activation lacks its trusted capability closure', blockers=gate.blockers),) if gate.blockers else ()
    return OracleNode(node_id=node_id, kind='activated_ability', text=line, span=span,
        active_zone='hand', event='activate', lowerable=True, exact=not residual_ids,
        template_id='fixed-transmute-activation-v1', cost=activated_ability_cost(ability),
        effects=(transmute_search_effect(),), handlers=(activated_ability_catalog_descriptor(ability, requires_complete_card_program=True),),
        mechanics=mechanics, residual_ids=residual_ids, capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure is not None else (),
        capability_profile=gate.closure.profile if gate.closure is not None else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure is not None else None)
