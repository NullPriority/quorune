from __future__ import annotations

"""Compose an independently closed casting price, payment keywords and result."""

from dataclasses import replace
from .ir_model import OracleFaceIR,append_residual
from .spell_additional_cost_nodes import typed_additional_cost_spell_node
from .dependency_gate import dependency_gate
from ..flashback import FLASHBACK_MECHANIC_ID, compile_fixed_mana_flashback
from ..rules.tap_additional_cost import PAYMENT_COMPOSITION_MECHANIC


def _has_fixed_discard_with_mana_flashback(rows):
    if len(rows) != 3 or " ".join(rows[0][1].split()).casefold() != (
        "as an additional cost to cast this spell, discard a card."
    ):
        return False
    line, material, span = rows[-1]
    flashback = compile_fixed_mana_flashback(
        material_line=material, oracle_line=line, line_index=span.line - 1,
    )
    return flashback is not None and flashback.life_payment is None


def _nonland_sacrifice_flashback_face(
    *, record, face_id, face_name, oracle_text, material_rows, effect_template,
    trusted_mechanics, capability_registry, capability_profile, residuals,
):
    from ..flashback import FLASHBACK_MECHANIC_ID, compile_fixed_mana_flashback
    from .flashback_nodes import ordinary_fixed_mana_flashback_keyword_node

    if len(material_rows) != 3 or material_rows[0][1].strip().casefold() != (
        "as an additional cost to cast this spell, sacrifice a nonland permanent."
    ):
        return None

    line, material, span = material_rows[-1]
    spec = compile_fixed_mana_flashback(material_line=material, oracle_line=line, line_index=span.line - 1)
    if spec is None or spec.life_payment is not None:
        return None

    spell = typed_additional_cost_spell_node(
        node_id=f"{face_id}:n1", rows=material_rows[:2],
        card_name=face_name or record.name, effect_template=effect_template,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals,
    )
    if spell is None:
        return None
    flashback = ordinary_fixed_mana_flashback_keyword_node(
        node_id=f"{face_id}:n2", line=line, material_line=material, span=span,
        mechanics=(FLASHBACK_MECHANIC_ID,), capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals,
    )
    return OracleFaceIR(
        face_id=face_id, face_name=face_name, oracle_text=oracle_text,
        nodes=(spell, flashback), residuals=tuple(residuals),
    )


def compile_additional_cost_payment_face(*,record,face_id,face_name,oracle_text,material_rows,
        effect_template,trusted_mechanics,capability_registry,capability_profile,residuals):
    from .gift_card_spells import compile_gift_card_face

    gift = compile_gift_card_face(
        record=record, face_id=face_id, face_name=face_name, oracle_text=oracle_text,
        material_rows=material_rows, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals,
    )
    if gift is not None:
        return gift
    from .keyword_nodes import ordinary_convoke_keyword_node
    from .casting_payment_keyword_nodes import ordinary_improvise_keyword_node,ordinary_delve_keyword_node
    lowerers={'convoke':ordinary_convoke_keyword_node,'improvise':ordinary_improvise_keyword_node,'delve':ordinary_delve_keyword_node}
    fixed_discard_flashback = _has_fixed_discard_with_mana_flashback(material_rows)
    if fixed_discard_flashback:
        payments, remaining = material_rows[-1:], material_rows[:-1]
    else:
        payments = [row for row in material_rows if row[1].strip().rstrip('.').casefold() in lowerers]
        remaining = [row for row in material_rows if row not in payments]
    if not payments:
        return _nonland_sacrifice_flashback_face(
            record=record, face_id=face_id, face_name=face_name, oracle_text=oracle_text,
            material_rows=material_rows, effect_template=effect_template,
            trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
            capability_profile=capability_profile, residuals=residuals,
        )
    if not remaining or not remaining[0][1].casefold().startswith('as an additional cost to cast this spell,'):return None
    node=typed_additional_cost_spell_node(node_id=f'{face_id}:cost',rows=remaining,card_name=face_name or record.name,
        effect_template=effect_template,trusted_mechanics=trusted_mechanics,capability_registry=capability_registry,
        capability_profile=capability_profile,residuals=residuals)
    if node is None:return None
    if fixed_discard_flashback:
        from .flashback_nodes import ordinary_fixed_mana_flashback_keyword_node

        line, material, span = payments[0]
        keyword = ordinary_fixed_mana_flashback_keyword_node(
            node_id=f"{face_id}:flashback", line=line, material_line=material, span=span,
            mechanics=(FLASHBACK_MECHANIC_ID,), capability_registry=capability_registry,
            capability_profile=capability_profile, residuals=residuals,
        )
        return OracleFaceIR(
            face_id=face_id, face_name=face_name, oracle_text=oracle_text,
            nodes=(node, keyword), residuals=tuple(residuals),
        )
    mechanics=(*node.mechanics,PAYMENT_COMPOSITION_MECHANIC)
    gate=dependency_gate(mechanics=mechanics,effects=node.effects,target_schema=node.target_schema,cost_schema=node.cost,
        trusted_mechanics=trusted_mechanics,capability_registry=capability_registry,capability_profile=capability_profile)
    added=[]
    from ..rules.casting_additional_cost_groups import announced_additional_cost_dependencies
    if announced_additional_cost_dependencies(node.cost):
        added.append(append_residual(residuals,kind='spell_payment_composition',text=oracle_text[node.span.start:node.span.end],span=node.span,
            reason='Declared-X additional prices with payment keywords need a shared dependent payment plan',blockers=('declared-X payment-keyword composition',)))
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


def compile_typed_additional_cost_face(
    *, record, face_id, face_name, type_line, keywords, oracle_text, material_rows,
    effect_template, compile_inner, trigger_node, grant_effect_templates,
    trusted_mechanics, capability_registry, capability_profile, residuals,
):
    """Assemble the represented casting price and its independently closed result."""
    from .creature_sacrifice_credit import compile_sacrifice_credit_with_token

    context = {
        "effect_template": effect_template, "trusted_mechanics": trusted_mechanics,
        "capability_registry": capability_registry, "capability_profile": capability_profile,
        "residuals": residuals,
    }
    face = {
        "record": record, "face_id": face_id, "face_name": face_name,
        "oracle_text": oracle_text, "material_rows": material_rows,
    }
    priced_token = compile_sacrifice_credit_with_token(
        **face, **context, type_line=type_line, keywords=keywords, compile_inner=compile_inner,
        trigger_node=trigger_node, grant_effect_templates=grant_effect_templates,
    )
    if priced_token is not None:
        return priced_token
    composed = compile_additional_cost_payment_face(**face, **context)
    if composed is not None:
        return composed
    from .spell_additional_cost_nodes import paid_sacrifice_token_ability_nodes

    token_nodes = paid_sacrifice_token_ability_nodes(
        record=record, face_id=face_id, rows=material_rows, card_name=face_name or record.name,
        effect_template=effect_template, trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry, capability_profile=capability_profile,
        compile_inner=compile_inner, trigger_node=trigger_node, grant_effect_templates=grant_effect_templates,
    )
    if token_nodes is not None:
        return OracleFaceIR(
            face_id=face_id, face_name=face_name, oracle_text=oracle_text,
            nodes=token_nodes, residuals=tuple(residuals),
        )
    node = typed_additional_cost_spell_node(
        node_id=f"{face_id}:n1", rows=material_rows, card_name=face_name or record.name, **context,
    )
    if node is None:
        return None
    return OracleFaceIR(
        face_id=face_id, face_name=face_name, oracle_text=oracle_text,
        nodes=(node,), residuals=tuple(residuals),
    )
