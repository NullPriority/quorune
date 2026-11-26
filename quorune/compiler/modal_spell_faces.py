from __future__ import annotations

"""Whole modal spell assembly with independently retained token child nodes."""

from ..characteristic_evaluation import type_parts
from .dependency_gate import dependency_gate
from .ir_model import OracleFaceIR, OracleNode, SourceSpan, append_residual
from .modal_token_branches import ModalTokenBranchCompiler


def _modal_keyword_siblings(rows, *, record, face_id, face_name, type_line, keywords,
                            effect_template, trusted_mechanics, capability_registry,
                            capability_profile, compile_keyword):
    starts=[index for index,row in enumerate(rows) if row[1].startswith('Choose ')]
    if len(starts)!=1:return None
    start=starts[0];end=start+1
    while end<len(rows) and rows[end][1].startswith('• '):end+=1
    if end==start+1 or end-start==len(rows):return None
    types,subtypes,_supertypes=type_parts(type_line);siblings=[];scratch=[]
    for index,(line,material,span) in enumerate(rows):
        if start<=index<end:continue
        nodes=compile_keyword(record=record,face_id=face_id,node_id=f'{face_id}:n{index+1}',
            line=line,material_line=material,card_name=face_name or record.name,effect_template=effect_template,
            span=span,keywords=keywords,printed_card_types=tuple(sorted(types)),printed_subtypes=tuple(sorted(subtypes)),
            saga_chapters=(),printed_power=None if record.faces else record.power,trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry,capability_profile=capability_profile,residuals=scratch)
        if not nodes or scratch or any(not node.exact or node.kind=='spell_ability' for node in nodes):return None
        siblings.extend(nodes)
    return rows[start:end],tuple(siblings)


def compile_whole_modal_face(*, record, face_id, face_name, type_line, keywords, oracle_text,
                            material_rows, effect_template, trusted_mechanics, capability_registry,
                            capability_profile, residuals, compile_inner, trigger_node, grant_effect_templates,
                            compile_keyword=None):
    from .whole_modal_templates import whole_modal_spell_template
    from .own_combat_modal_templates import split_own_combat_modal

    original_rows=material_rows;siblings=()
    restricted = split_own_combat_modal(
        material_rows, face_id=face_id, source_name=face_name or record.name, effect_template=effect_template,
        capability_registry=capability_registry, capability_profile=capability_profile,
    )
    if restricted is not None:
        material_rows,siblings,effect_template=restricted
    arguments=dict(record=record, face_id=face_id, source_name=face_name or record.name,
        effect_template=effect_template, printed_card_types=tuple(sorted(type_parts(type_line)[0])),
        keywords=keywords, trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, compile_inner=compile_inner, trigger_node=trigger_node,
        grant_effect_templates=grant_effect_templates)
    branches = ModalTokenBranchCompiler(material_rows=material_rows,**arguments)
    modal_template = whole_modal_spell_template(material_rows, compile_effect=branches)
    if modal_template is None and restricted is None and compile_keyword is not None:
        separated=_modal_keyword_siblings(material_rows,record=record,face_id=face_id,face_name=face_name,
            type_line=type_line,keywords=keywords,effect_template=effect_template,trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry,capability_profile=capability_profile,compile_keyword=compile_keyword)
        if separated is not None:
            material_rows,siblings=separated
            branches=ModalTokenBranchCompiler(material_rows=material_rows,**arguments)
            modal_template=whole_modal_spell_template(material_rows,compile_effect=branches)
    if modal_template is None:
        return None
    template, effects, target_schema, mechanics = modal_template.compiled()
    cost_schema = getattr(modal_template, 'cost_schema', None)
    from ..kicked_modal import KICKED_MODAL_FIELD
    from ..kicker import KICKER_COST_HANDLER_ID

    if cost_schema and KICKED_MODAL_FIELD in cost_schema:
        kicker = [handler for node in siblings for handler in node.handlers
                  if handler.get("handler_id") == KICKER_COST_HANDLER_ID]
        if len(kicker) != 1 or record.layout != "normal" or record.faces:
            return None
    gate = dependency_gate(
        mechanics=mechanics,
        effects=effects,
        target_schema=target_schema,
        cost_schema=cost_schema,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    span = SourceSpan(
        material_rows[0][2].start,
        material_rows[-1][2].end,
        material_rows[0][2].line,
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=oracle_text,
                span=span,
                reason="fixed modal spell depends on untrusted rules dependencies",
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    node = OracleNode(
        node_id=f"{face_id}:n{original_rows.index(material_rows[0])+1}",
        kind="spell_ability",
        text=oracle_text[span.start:span.end] if siblings else oracle_text,
        span=span,
        active_zone="stack",
        event="resolve",
        lowerable=True,
        exact=not residual_ids,
        template_id=template,
        effects=effects,
        target_schema=target_schema,
        mechanics=mechanics,
        cost=cost_schema,
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            gate.closure.reachable
            if gate.closure is not None
            else ()
        ),
        capability_profile=(
            gate.closure.profile
            if gate.closure is not None
            else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint
            if gate.closure is not None
            else None
        ),
    )
    return OracleFaceIR(
        face_id=face_id,
        face_name=face_name,
        oracle_text=oracle_text,
        nodes=tuple(sorted((node,*branches.children,*siblings),key=lambda item:(item.span.start,item.node_id))) if siblings else (node,*branches.children),
        residuals=tuple(residuals),
    )
