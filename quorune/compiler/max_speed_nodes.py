from __future__ import annotations

"""Gate exact Max Speed child abilities through shared layer-six scopes."""

from dataclasses import replace
import re
from ..abilities import parse_activated_abilities
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE,STATIC_COMPONENT_SCOPE_FRAGMENT_HANDLER_ID,ability_fragment_to_dict
from ..static_component_scope import StaticComponentScopeSpec,StaticComponentApplicabilitySpec
from ..rules.player_speed_model import SPEED_CAPABILITY
from ..semantic_runtime.activated_abilities import activated_ability_catalog_descriptor,ACTIVATED_ABILITY_CATALOG_HANDLER_ID
from .activated_costs import activated_ability_cost_capabilities
from .generated_program_identity import generated_ability_id
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode,append_residual


def max_speed_nodes(*,record,face_id,node_id,line,material_line,span,source_name,keywords,printed_card_types,trusted_mechanics,capability_registry,capability_profile,residuals,compile_inner,effect_template,trigger_effect_template,material_line_for):
    match=re.fullmatch(r'Max speed\s*[—�]\s*(?P<body>.+)',material_line,re.I)
    if match is None:return None
    body=match['body'];inner_residuals=[]
    inner=compile_inner(node_id=node_id+':max-speed-child',line=body,material_line=material_line_for(body),span=span,
        card_name=source_name,type_line='Creature — Fixture',keywords=keywords,trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=inner_residuals,
        effect_template=effect_template,trigger_effect_template=trigger_effect_template)
    if inner is None:
        from .static_runtime_nodes import static_runtime_node
        inner=static_runtime_node(node_id=node_id+':max-speed-child',line=body,material_line=material_line_for(body),span=span,source_name=source_name,
            card_types=set(printed_card_types),permanent_card_types={'artifact','battle','creature','enchantment','land','planeswalker'},source_is_class=False,
            capability_registry=capability_registry,capability_profile=capability_profile,residuals=inner_residuals)
    if inner is None or not inner.exact or inner_residuals:return None
    if inner.kind not in {'activated_ability','mana_ability','triggered_ability','replacement_effect','static_ability'}:return None
    caps=set(inner.capability_dependencies)|{SPEED_CAPABILITY}
    if inner.kind=='activated_ability' and not any(h.get('handler_id')==ACTIVATED_ABILITY_CATALOG_HANDLER_ID for h in inner.handlers):
        abilities=parse_activated_abilities(card_name=source_name,oracle_text=body,keywords=keywords)
        if len(abilities)!=1 or not abilities[0].compiled_cost:return None
        ability=replace(abilities[0],ability_id=f'ab{span.line}',line_index=span.line-1,oracle_line=line)
        caps.update(activated_ability_cost_capabilities(ability))
        inner=replace(inner,handlers=(*inner.handlers,activated_ability_catalog_descriptor(ability)))
    child_id=generated_ability_id(kind=inner.kind,face_id=face_id,line=span.line,static_declaration=bool(inner.handlers),node_id=inner.node_id)
    parent_node_id=node_id+':max-speed-scope'
    parent_id=generated_ability_id(kind='static_ability',face_id=face_id,line=span.line,static_declaration=True,node_id=parent_node_id)
    if not child_id or not parent_id:return None
    scope=StaticComponentScopeSpec(parent_semantic_key=f'{record.oracle_id}:{parent_id}',child_semantic_keys=(f'{record.oracle_id}:{child_id}',),keywords=(),
        applicability=StaticComponentApplicabilitySpec('controller_speed_equals','controller_speed',4))
    gate=explicit_capabilities_gate(tuple(sorted(caps)),capability_registry=capability_registry,capability_profile=capability_profile)
    ids=(append_residual(residuals,kind='dependency_contract',text=line,span=span,reason='Max Speed child requires player speed and its independent inner program.',blockers=gate.blockers),) if gate.blockers else ()
    parent=OracleNode(node_id=parent_node_id,kind='static_ability',text=line,span=span,active_zone='all',event='characteristics.evaluate',lowerable=True,exact=not ids,template_id='max-speed-component-scope-v1',
        handlers=({'handler_id':STATIC_COMPONENT_SCOPE_FRAGMENT_HANDLER_ID,'schema_version':1,'event':'characteristics.evaluate','fragment':ability_fragment_to_dict(scope)},),
        mechanics=('max speed',),runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),residual_ids=ids,capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),capability_profile=gate.closure.profile if gate.closure else None,capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
    child=replace(inner,node_id=node_id+':max-speed-child',text=line,runtime_coverage=tuple(dict.fromkeys((*inner.runtime_coverage,CURRENT_ABILITY_FRAGMENT_COVERAGE))))
    return parent,child
