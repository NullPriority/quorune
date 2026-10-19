from __future__ import annotations

"""Source-spanned copy additions over independently admitted child nodes."""

from dataclasses import replace
import re
from ..ability_fragments import ability_fragment_to_dict
from ..semantic_runtime.ability_fragments import fragments_from_descriptors
from .attached_granted_ability_nodes import attached_granted_ability_plan
from .entry_copy_templates import entry_copy_node,entry_copy_spec


def entry_copy_ability_nodes(*,record,face_id,node_id,line,material_line,span,
        source_name,trusted_mechanics,capability_registry,capability_profile,
        compile_inner,effect_template,trigger_effect_template,material_line_for,keyword_node_compiler):
    if capability_registry is None:return None
    match=re.fullmatch(r'(?P<shell>You may have .+? enter as a copy of .+?), except '
        r'(?:(?P<exception>[^\"]+?) and )?it has "(?P<ability>[^\"]+)"\.?',material_line,re.I)
    keyword_match=None
    if match is None:
        keyword_match=re.fullmatch(r'(?P<shell>You may have .+? enter as a copy of .+?), except '
            r'(?:(?P<exception>.+?)(?:, and|,| and) )?(?:it has|has) (?P<ability>haste and dethrone|myriad|changeling)\.?',material_line,re.I)
        match=keyword_match
    crew_shell=None
    if match is None:
        crew_shell=re.fullmatch(r"(?P<shell>You may have .+? enter as a copy of .+?), except it's a Vehicle artifact with crew 3 and it loses all other card types\.?",material_line,re.I)
        if crew_shell is None:return None
    quoted='Crew 3' if crew_shell else match['ability']
    if source_name.casefold() in quoted.casefold():return None
    shell=crew_shell['shell']+", except it's a Vehicle artifact and it loses all other card types" if crew_shell else match['shell']+(', except '+match['exception'] if match['exception'] else '')
    shell=re.sub(r"it isn't legendary, is an artifact in addition to its other types$","it isn't legendary and it's an artifact in addition to its other types",shell,flags=re.I)
    spec=entry_copy_spec(shell,source_name=source_name)
    if spec is None or spec.selection.types_all!=('creature',):return None
    residuals=[];child_id=node_id+':copy-ability';children=[];fragments=[];added_keywords=()
    if keyword_match or crew_shell:
        added_keywords=('Haste',) if quoted.casefold()=='haste and dethrone' else ()
        keyword='Crew 3' if crew_shell else 'Dethrone' if added_keywords else 'Changeling' if quoted.casefold()=='changeling' else 'Myriad'
        compiled=keyword_node_compiler(record=record,face_id=face_id,node_id=child_id,line=keyword,
            material_line=keyword,card_name='Copied creature',effect_template=effect_template,span=span,
            keywords=('Crew',) if crew_shell else (keyword,),printed_card_types=('artifact',) if crew_shell else ('creature',),printed_subtypes=(),saga_chapters=(),printed_power='0',
            trusted_mechanics=trusted_mechanics,capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
        if not compiled or residuals or any(not inner.exact for inner in compiled):return None
        for inner in compiled:
            if crew_shell:
                plan=attached_granted_ability_plan(node=inner,quoted_text=keyword,oracle_id=record.oracle_id,face_id=face_id,source_line=span.line,card_name='Copied creature',keywords=('Crew',),node_id=child_id)
                if plan is None:return None
                fragments.append(plan.fragment);children.append(replace(inner,node_id=child_id,kind=plan.node_kind))
                continue
            static=fragments_from_descriptors(inner.handlers)
            if static:fragments.extend(ability_fragment_to_dict(value) for value in static)
            elif inner.kind=='triggered_ability':
                plan=attached_granted_ability_plan(node=inner,quoted_text=keyword,oracle_id=record.oracle_id,face_id=face_id,
                    source_line=span.line,card_name='Copied creature',keywords=(keyword,),node_id=child_id)
                if plan is None:return None
                from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
                fragments.append(plan.fragment);children.append(replace(inner,node_id=child_id,kind=plan.node_kind,
                    runtime_coverage=tuple(v for v in inner.runtime_coverage if v!=CURRENT_ABILITY_FRAGMENT_COVERAGE)))
            else:return None
    else:
        inner=compile_inner(node_id=child_id,line=quoted,material_line=material_line_for(quoted),
            span=span,card_name='Copied creature',type_line='Artifact' if spec.exception.card_types==('artifact',) else 'Creature',keywords=(),
            trusted_mechanics=trusted_mechanics,capability_registry=capability_registry,
            capability_profile=capability_profile,residuals=residuals,
            effect_template=effect_template,trigger_effect_template=trigger_effect_template)
        if inner is None or not inner.exact or residuals:return None
        plan=attached_granted_ability_plan(node=inner,quoted_text=quoted,oracle_id=record.oracle_id,
            face_id=face_id,source_line=span.line,card_name='Copied creature',keywords=(),node_id=child_id)
        if plan is None:return None
        fragments.append(plan.fragment);children.append(replace(inner,node_id=child_id,kind=plan.node_kind,text=quoted,span=span,residual_ids=()))
        compiled=(inner,)
    outer=entry_copy_node(node_id=node_id,line=line,material_line=shell,span=span,
        source_name=source_name,capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    if outer is None or not outer.exact or residuals:return None
    copied=replace(spec,exception=replace(spec.exception,add_ability_fragments=tuple(fragments),
        add_keywords=tuple(dict.fromkeys((*spec.exception.add_keywords,*added_keywords)))))
    handlers=tuple({**dict(handler),'copy':copied.to_dict()} for handler in outer.handlers)
    from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
    dependencies=tuple(sorted(set(outer.capability_dependencies)|{value for inner in compiled for value in inner.capability_dependencies}
        |{value for keyword in added_keywords for value in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword]}))
    closure=capability_registry.closure(dependencies,profile=capability_profile)
    if not closure.trusted or closure.blockers:return None
    return (replace(outer,template_id='intrinsic-entry-copiable-ability-v1',handlers=handlers,
        capability_dependencies=dependencies,capability_closure=closure.reachable,
        capability_profile=closure.profile,capability_fingerprint=closure.fingerprint),*children)
