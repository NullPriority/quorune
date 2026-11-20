from __future__ import annotations

"""Intrinsic Tribute and its source-bound unpaid-entry production."""

from . import regex_working_set as re
from ..tribute_model import TributeSpec,TRIBUTE_CAPABILITY,TRIBUTE_UNPAID_FIELD
from ..rules.source_references import SourceReferenceSpec,source_self_permanent_type
from .dependency_gate import explicit_capability_gate,dependency_gate
from .ir_model import OracleNode,append_residual


def tribute_keyword_node(*,node_id,line,material_line,span,capability_registry,capability_profile,residuals,**_):
    match=re.fullmatch(r'Tribute ([1-9]\d*)\.?',material_line.strip(),re.I)
    if match is None:return None
    spec=TributeSpec(int(match[1]),'tribute')
    gate=explicit_capability_gate(TRIBUTE_CAPABILITY,capability_registry=capability_registry,capability_profile=capability_profile)
    ids=() if not gate.blockers else (append_residual(residuals,kind='dependency_contract',text=line,span=span,
        reason='Tribute entry decisions require their owned capability',blockers=gate.blockers),)
    closure=gate.closure
    return OracleNode(node_id=node_id,kind='keyword_ability',text=line,span=span,active_zone='all',event='zone.change',
        lowerable=True,exact=not ids,template_id='tribute-entry-choice-v1',handlers=(spec.descriptor(),),
        mechanics=('tribute',),residual_ids=ids,capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure else (),capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None)


def tribute_unpaid_entry_node(*,node_id,line,material_line,span,card_name,effect_template,
        trusted_mechanics,capability_registry,capability_profile,residuals,**_):
    match=re.fullmatch(r"When (.+?) enters, if tribute wasn't paid, (.+)",material_line.strip(),re.I)
    if match is None or not(source_self_permanent_type(match[1]) or SourceReferenceSpec(card_name).matches(match[1])):
        return None
    body=re.sub(r'^it (?=deals|gets|gains)',card_name+' ',match[2],flags=re.I)
    template,effects,schema,result_mechanics=effect_template(body,card_name=card_name)
    if template is None:return None
    mechanics=('cr-603-handling-triggered-abilities','fixed-typed-event-effect-trigger',*result_mechanics)
    gate=dependency_gate(mechanics=mechanics,effects=effects,target_schema=schema,trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,capability_profile=capability_profile,explicit_capabilities=(TRIBUTE_CAPABILITY,))
    ids=() if not gate.blockers else (append_residual(residuals,kind='dependency_contract',text=line,span=span,
        reason='Unpaid Tribute requires sealed entry facts and closed result owners',blockers=gate.blockers),)
    closure=gate.closure
    return OracleNode(node_id=node_id,kind='triggered_ability',text=line,span=span,active_zone='battlefield',
        event='permanent.enter.self',event_condition={'field':TRIBUTE_UNPAID_FIELD,'op':'contains_any','value':['tribute']},
        lowerable=True,exact=not ids,template_id='tribute-unpaid-entry-v1',effects=effects,target_schema=schema,
        mechanics=mechanics,residual_ids=ids,capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure else (),capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None)
