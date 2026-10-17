from __future__ import annotations

"""Source-spanned copiable creature-entry grants over exact inner programs."""

from dataclasses import replace
import re

from .attached_granted_ability_nodes import attached_granted_ability_plan
from .entry_copy_templates import entry_copy_node,entry_copy_spec


def entry_copy_ability_nodes(*,record,face_id,node_id,line,material_line,span,
        source_name,trusted_mechanics,capability_registry,capability_profile,
        compile_inner,effect_template,trigger_effect_template,material_line_for):
    match=re.fullmatch(r'(?P<shell>You may have .+? enter as a copy of .+?), except '
        r'(?:(?P<exception>[^\"]+?) and )?it has "(?P<ability>[^\"]+)"\.?',material_line,re.I)
    if match is None or capability_registry is None:return None
    quoted=match['ability']
    if source_name.casefold() in quoted.casefold():return None
    shell=match['shell']+(', except '+match['exception'] if match['exception'] else '')
    spec=entry_copy_spec(shell,source_name=source_name)
    if spec is None or spec.selection.types_all!=('creature',):return None
    residuals=[]
    child_id=node_id+':copy-ability'
    inner=compile_inner(node_id=child_id,line=quoted,material_line=material_line_for(quoted),
        span=span,card_name='Copied creature',type_line='Creature',keywords=(),
        trusted_mechanics=trusted_mechanics,capability_registry=capability_registry,
        capability_profile=capability_profile,residuals=residuals,
        effect_template=effect_template,trigger_effect_template=trigger_effect_template)
    if inner is None or not inner.exact or residuals:return None
    plan=attached_granted_ability_plan(node=inner,quoted_text=quoted,oracle_id=record.oracle_id,
        face_id=face_id,source_line=span.line,card_name='Copied creature',keywords=(),node_id=child_id)
    if plan is None:return None
    outer=entry_copy_node(node_id=node_id,line=line,material_line=shell,span=span,
        source_name=source_name,capability_registry=capability_registry,
        capability_profile=capability_profile,residuals=residuals)
    if outer is None or not outer.exact or residuals:return None
    copied=replace(spec,exception=replace(spec.exception,add_ability_fragments=(plan.fragment,)))
    handlers=tuple({**dict(handler),'copy':copied.to_dict()} for handler in outer.handlers)
    dependencies=tuple(sorted(set(outer.capability_dependencies)|set(inner.capability_dependencies)))
    closure=capability_registry.closure(dependencies,profile=capability_profile)
    if not closure.trusted or closure.blockers:return None
    return (replace(outer,template_id='intrinsic-entry-copiable-ability-v1',handlers=handlers,
        capability_dependencies=dependencies,capability_closure=closure.reachable,
        capability_profile=closure.profile,capability_fingerprint=closure.fingerprint),
        replace(inner,node_id=child_id,kind=plan.node_kind,text=quoted,span=span,residual_ids=()))
