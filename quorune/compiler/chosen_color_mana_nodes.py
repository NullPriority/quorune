from __future__ import annotations

"""Lower source-chosen color output with independently closed activation costs."""

from ..chosen_color_mana_abilities import compile_chosen_color_activated_mana_ability, chosen_color_mana_handler_descriptor
from ..chosen_color_mana_model import CHOSEN_COLOR_MANA_CAPABILITY
from .activated_costs import activated_ability_cost, activated_ability_cost_capabilities
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual


def chosen_color_activated_mana_node(ability, node_id, line, span, capability_registry, capability_profile, residuals):
    spec = compile_chosen_color_activated_mana_ability(ability)
    if spec is None:
        return ability, None
    gate = explicit_capabilities_gate((CHOSEN_COLOR_MANA_CAPABILITY, *activated_ability_cost_capabilities(ability)),
        capability_registry=capability_registry, capability_profile=capability_profile)
    residual_ids = (append_residual(residuals, kind='dependency_contract', text=line, span=span,
        reason='Source chosen-color mana lacks trusted output/cost ownership', blockers=gate.blockers),) if gate.blockers else ()
    return ability, OracleNode(node_id=node_id, kind='mana_ability', text=line, span=span, active_zone='battlefield', event='activate',
        lowerable=True, exact=not gate.blockers, template_id='activated-mana-source-chosen-color-v1', cost=activated_ability_cost(ability),
        handlers=(chosen_color_mana_handler_descriptor(spec),), mechanics=(), residual_ids=residual_ids,
        capability_dependencies=gate.capabilities, capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None, capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
