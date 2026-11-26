from __future__ import annotations

"""Lower public-quantity mana with separately closed activation costs."""

from ..public_quantity_mana_abilities import (
    compile_public_quantity_activated_mana_ability,
    public_quantity_mana_handler_descriptor,
)
from ..characteristic_fragments import characteristic_quantity_capabilities
from ..object_predicate import object_query_capabilities
from ..fixed_mana_abilities import _without_activation_restriction
from .paid_sacrifice_amount_templates import paid_sacrifice_activation_mana_output
from .public_quantity_mana import (
    chosen_devotion_mana_template, chosen_subtype_mana_template,
    controlled_land_subtype_pair_mana_template, public_quantity_mana_template,
)
from .activated_costs import activated_ability_cost, activated_ability_cost_capabilities
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual


def public_quantity_activated_mana_node(ability,node_id,line,span,capability_registry,capability_profile,residuals,*,source_name):
    quantity_text = _without_activation_restriction(
        ability, " ".join(ability.effect_text.split()), source_name=source_name,
    )
    output = (paid_sacrifice_activation_mana_output(ability)
              or chosen_devotion_mana_template(ability.effect_text)
              or chosen_subtype_mana_template(ability.effect_text)
              or controlled_land_subtype_pair_mana_template(ability.effect_text)
              or (public_quantity_mana_template(quantity_text, source_name=source_name)
                  if quantity_text is not None else None))
    if output is None:
        from .land_play_mana_nodes import land_play_activated_mana_node

        land_play = land_play_activated_mana_node(
            ability, node_id, line, span, capability_registry, capability_profile, residuals,
        )
        if land_play is not None:
            return ability, land_play
        from .artifact_hand_mana_nodes import artifact_hand_mana_activated_node
        artifact_hand = artifact_hand_mana_activated_node(
            ability, node_id, line, span, capability_registry, capability_profile, residuals,
        )
        if artifact_hand is not None:
            return artifact_hand
        from .chosen_color_mana_nodes import chosen_color_activated_mana_node
        return chosen_color_activated_mana_node(ability, node_id, line, span, capability_registry, capability_profile, residuals)
    spec=compile_public_quantity_activated_mana_ability(ability,output)
    if spec is None:
        return ability,None
    capabilities=(*output.capability_dependencies,*activated_ability_cost_capabilities(ability),
        *characteristic_quantity_capabilities(output.amount.to_dict()),
        *object_query_capabilities(output.amount.to_dict()))
    gate=explicit_capabilities_gate(capabilities,capability_registry=capability_registry,capability_profile=capability_profile)
    residual_ids=(append_residual(residuals,kind='dependency_contract',text=line,span=span,
        reason='Public quantity activated mana lacks trusted capability closure',blockers=gate.blockers),) if gate.blockers else ()
    return ability,OracleNode(node_id=node_id,kind='mana_ability',text=line,span=span,active_zone='battlefield',event='activate',
        lowerable=True,exact=not gate.blockers,template_id='activated-mana-public-quantity-v1',cost=activated_ability_cost(ability),
        handlers=(public_quantity_mana_handler_descriptor(spec),),mechanics=(),residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
