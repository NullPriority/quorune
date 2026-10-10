from __future__ import annotations

"""Lower public-quantity mana with separately closed activation costs."""

from ..public_quantity_mana_abilities import (
    compile_public_quantity_activated_mana_ability,
    public_quantity_mana_handler_descriptor,
)
from ..public_quantity_mana_model import PUBLIC_QUANTITY_MANA_CAPABILITY
from .public_quantity_mana import public_quantity_mana_template
from .activated_costs import activated_ability_cost, activated_ability_cost_capabilities
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual


def public_quantity_activated_mana_node(ability,node_id,line,span,capability_registry,capability_profile,residuals,*,source_name):
    output=public_quantity_mana_template(ability.effect_text,source_name=source_name)
    if output is None:
        return ability,None
    spec=compile_public_quantity_activated_mana_ability(ability,output)
    if spec is None:
        return ability,None
    capabilities=(PUBLIC_QUANTITY_MANA_CAPABILITY,*activated_ability_cost_capabilities(ability))
    gate=explicit_capabilities_gate(capabilities,capability_registry=capability_registry,capability_profile=capability_profile)
    residual_ids=(append_residual(residuals,kind='dependency_contract',text=line,span=span,
        reason='Public quantity activated mana lacks trusted capability closure',blockers=gate.blockers),) if gate.blockers else ()
    return ability,OracleNode(node_id=node_id,kind='mana_ability',text=line,span=span,active_zone='battlefield',event='activate',
        lowerable=True,exact=not gate.blockers,template_id='activated-mana-public-quantity-v1',cost=activated_ability_cost(ability),
        handlers=(public_quantity_mana_handler_descriptor(spec),),mechanics=(),residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
