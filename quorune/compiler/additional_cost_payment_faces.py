from __future__ import annotations

"""Compose an independently closed casting price, payment keywords and result."""

from dataclasses import replace
from .ir_model import OracleFaceIR,append_residual
from .spell_additional_cost_nodes import typed_additional_cost_spell_node
from .dependency_gate import dependency_gate
from ..rules.tap_additional_cost import PAYMENT_COMPOSITION_MECHANIC


def compile_additional_cost_payment_face(*,record,face_id,face_name,oracle_text,material_rows,
        effect_template,trusted_mechanics,capability_registry,capability_profile,residuals):
    from .keyword_nodes import ordinary_convoke_keyword_node
    from .casting_payment_keyword_nodes import ordinary_improvise_keyword_node,ordinary_delve_keyword_node
    lowerers={'convoke':ordinary_convoke_keyword_node,'improvise':ordinary_improvise_keyword_node,'delve':ordinary_delve_keyword_node}
    payments=[row for row in material_rows if row[1].strip().rstrip('.').casefold() in lowerers]
    remaining=[row for row in material_rows if row not in payments]
    if not payments or not remaining or not remaining[0][1].casefold().startswith('as an additional cost to cast this spell,'):return None
    node=typed_additional_cost_spell_node(node_id=f'{face_id}:cost',rows=remaining,card_name=face_name or record.name,
        effect_template=effect_template,trusted_mechanics=trusted_mechanics,capability_registry=capability_registry,
        capability_profile=capability_profile,residuals=residuals)
    if node is None:return None
    mechanics=(*node.mechanics,PAYMENT_COMPOSITION_MECHANIC)
    gate=dependency_gate(mechanics=mechanics,effects=node.effects,target_schema=node.target_schema,cost_schema=node.cost,
        trusted_mechanics=trusted_mechanics,capability_registry=capability_registry,capability_profile=capability_profile)
    added=[]
    if gate.blockers:
        added.append(append_residual(residuals,kind='dependency_contract',text=oracle_text[node.span.start:node.span.end],span=node.span,
            reason='Additional price and payment-keyword composition requires its producer and result capabilities',blockers=gate.blockers))
    if {'convoke','improvise'}<={row[1].strip().rstrip('.').casefold() for row in payments}:
        added.append(append_residual(residuals,kind='spell_payment_composition',text=oracle_text[node.span.start:node.span.end],span=node.span,
            reason='Concurrent Convoke and Improvise need a shared mixed tap-payment plan',blockers=('concurrent tap-payment mechanisms',)))
    node=replace(node,text=oracle_text[node.span.start:node.span.end],mechanics=mechanics,
        exact=node.exact and not added,residual_ids=(*node.residual_ids,*added),capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
    nodes=[node]
    for index,(line,material,span) in enumerate(payments,1):
        mechanic=material.strip().rstrip('.').casefold()
        nodes.append(lowerers[mechanic](node_id=f'{face_id}:payment:{index}',line=line,material_line=material,span=span,
            mechanics=(mechanic,),capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals))
    return OracleFaceIR(face_id=face_id,face_name=face_name,oracle_text=oracle_text,nodes=tuple(sorted(nodes,key=lambda n:n.span.start)),residuals=tuple(residuals))
