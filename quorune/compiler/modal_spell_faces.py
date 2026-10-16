from __future__ import annotations

"""Whole modal spell assembly with independently retained token child nodes."""

from ..characteristic_evaluation import type_parts
from .dependency_gate import dependency_gate
from .ir_model import OracleFaceIR, OracleNode, SourceSpan, append_residual
from .modal_token_branches import ModalTokenBranchCompiler


def compile_whole_modal_face(*, record, face_id, face_name, type_line, keywords, oracle_text,
                            material_rows, effect_template, trusted_mechanics, capability_registry,
                            capability_profile, residuals, compile_inner, trigger_node, grant_effect_templates):
    from .whole_modal_templates import whole_modal_spell_template
    branches = ModalTokenBranchCompiler(record=record, face_id=face_id, source_name=face_name or record.name,
        material_rows=material_rows, effect_template=effect_template, printed_card_types=tuple(sorted(type_parts(type_line)[0])),
        keywords=keywords, trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, compile_inner=compile_inner, trigger_node=trigger_node,
        grant_effect_templates=grant_effect_templates)
    modal_template = whole_modal_spell_template(material_rows, compile_effect=branches)
    if modal_template is None:
        return None
    template, effects, target_schema, mechanics = modal_template.compiled()
    cost_schema = getattr(modal_template, 'cost_schema', None)
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
        node_id=f"{face_id}:n1",
        kind="spell_ability",
        text=oracle_text,
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
        nodes=(node, *branches.children),
        residuals=tuple(residuals),
    )
