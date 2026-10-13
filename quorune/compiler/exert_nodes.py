from __future__ import annotations

"""Lower one source-spanned optional attack Exert and exact linked body."""

import re
from ..ability_fragments import ability_fragment_to_dict, CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..exert_model import ExertAttackSpec, EXERT_ATTACK_CAPABILITY, EXERT_UNTAP_CAPABILITY, EXERT_ATTACK_HANDLER
from ..rules.capabilities import capability_dependencies_for_node
from ..rules.source_references import SourceReferenceSpec
from .static_runtime_nodes import runtime_handler_node
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual
from .source_self_effect_templates import normalized_source_result_body, pin_source_result_incarnation


def exert_attack_nodes(*,record,face_id,node_id,line,material_line,span,source_name,
        effect_template,capability_registry,capability_profile,residuals):
    match=re.fullmatch(r'You may exert (?P<source>.+?) as it attacks\. When you do, (?P<body>.+)',material_line,re.I)
    if match is None or not (match['source'].casefold()=='this creature' or SourceReferenceSpec(source_name).matches(match['source'])):
        return None
    body=normalized_source_result_body(match['body']) or match['body']
    compiled=effect_template(body,card_name=source_name)
    if compiled[0] is None:
        return None
    compiled=pin_source_result_incarnation(compiled)
    template,effects,schema,mechanics=compiled
    key=f'{record.oracle_id}:trigger:{face_id}:n{span.line}'
    child=capability_dependencies_for_node(effects=effects,target_schema=schema,mechanic_ids=mechanics)
    if not child:
        return None
    outer=runtime_handler_node(node_id=node_id,line=line,span=span,
        compiled=('optional-exert-as-attacks-v1',{'handler_id':EXERT_ATTACK_HANDLER,'schema_version':1,
            'event':'characteristics.evaluate','fragment':ability_fragment_to_dict(ExertAttackSpec(key))},EXERT_ATTACK_CAPABILITY),
        kind='static_ability',event='characteristics.evaluate',runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason='Optional attack Exert requires a typed linked result and player-locked untap restriction.',
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    gate=explicit_capabilities_gate(tuple(sorted({EXERT_ATTACK_CAPABILITY,EXERT_UNTAP_CAPABILITY,'trigger.placement.apnap',*child})),
        capability_registry=capability_registry,capability_profile=capability_profile)
    ids=(append_residual(residuals,kind='dependency_contract',text=line,span=span,
        reason='Linked Exert result requires its independently closed body and timing owners.',blockers=gate.blockers),) if gate.blockers else ()
    result=OracleNode(node_id=node_id+':exert-result',kind='triggered_ability',text=line,span=span,
        active_zone='battlefield',event='permanent.exerted',lowerable=True,exact=not ids,
        template_id='linked-attack-exert-result-v1',effects=effects,target_schema=schema,
        event_condition={'all':[{'field':'card','op':'eq','value':'$source.ref'},
            {'field':'linked_exert_program','op':'eq','value':key}]},
        mechanics=mechanics,runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),residual_ids=ids,
        capability_dependencies=gate.capabilities,capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
    return outer,result
