from __future__ import annotations

"""Preserve complete spells with supported actual cast fact conditions."""

from .dependency_gate import dependency_gate
from .graveyard_cast_replacement import (
    MAIN_PHASE_CAST_ADDITIVE_TEMPLATE, MAIN_PHASE_CAST_DISCARD_TEMPLATE, MAIN_PHASE_CAST_PREPARATION_TEMPLATE,
    MAIN_PHASE_CAST_TEMPLATE, MAIN_PHASE_CAST_UNTAP_TEMPLATE, cast_main_phase_replacement_template,
)
from .ir_model import OracleFaceIR,OracleNode,SourceSpan,append_residual
from . import regex_working_set as re


def compile_cast_fact_spell_face(*,record,face_id,face_name,oracle_text,material_rows,effect_template,
        trusted_mechanics,capability_registry,capability_profile,residuals):
    if not material_rows or record.faces:return None
    material=' '.join(row[1] for row in material_rows)
    source_name = face_name or record.name
    from .named_cast_win_templates import named_cast_win_template
    from .player_capped_land_reveal import player_capped_land_reveal_template
    from .paid_mana_resolution_templates import independent_paid_mana_results_template


    compiled = (player_capped_land_reveal_template(material, mana_cost=record.mana_cost)
        or named_cast_win_template(material, source_name=source_name)
        or independent_paid_mana_results_template(
            material, source_name=source_name,
            compile_component=lambda body: effect_template(body, card_name=source_name),
        )
        or cast_main_phase_replacement_template(
        material, source_name=source_name, compile_component=lambda body: effect_template(body, card_name=source_name),
    ))
    if compiled is None:
        if not re.search(r'If at least .+? mana was spent to cast this spell,',material):return None
        compiled = effect_template(material, card_name=source_name)
        if compiled[0] not in {'paid-mana-same-target-replacement-v1','paid-mana-resolution-condition-v1'}:return None
    template,effects,schema,mechanics=compiled
    gate=dependency_gate(mechanics=mechanics,effects=effects,target_schema=schema,
        trusted_mechanics=trusted_mechanics,capability_registry=capability_registry,capability_profile=capability_profile)
    span=SourceSpan(material_rows[0][2].start,material_rows[-1][2].end,material_rows[0][2].line)
    reason = ('Main-phase cast result requires complete condition and child closure'
              if template in {MAIN_PHASE_CAST_TEMPLATE, MAIN_PHASE_CAST_ADDITIVE_TEMPLATE,
                              MAIN_PHASE_CAST_PREPARATION_TEMPLATE, MAIN_PHASE_CAST_UNTAP_TEMPLATE,
                              MAIN_PHASE_CAST_DISCARD_TEMPLATE}
              else 'Cast-dependent result requires complete condition and child closure')
    residual_ids=(append_residual(residuals,kind='dependency_contract',text=oracle_text,span=span,
        reason=reason,blockers=gate.blockers),) if gate.blockers else ()
    node=OracleNode(node_id=f'{face_id}:n1',kind='spell_ability',text=oracle_text,span=span,active_zone='stack',event='resolve',
        lowerable=True,exact=not residual_ids,template_id=template,effects=effects,target_schema=schema,mechanics=mechanics,
        residual_ids=residual_ids,capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
    return OracleFaceIR(face_id=face_id,face_name=face_name,oracle_text=oracle_text,nodes=(node,),residuals=tuple(residuals))
