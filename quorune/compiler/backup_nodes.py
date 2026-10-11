from __future__ import annotations

"""Compile Backup from complete, source-ordered downstream printed abilities."""

from dataclasses import replace
import re
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE,ability_fragment_to_dict
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
from ..rules.temporary_ability_grant import BACKUP_CAPABILITY,BACKUP_COVERAGE
from ..semantic_runtime.ability_fragments import default_ability_fragment_registry
from .attached_granted_ability_nodes import attached_granted_ability_plan
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode,append_residual


def _bundle(record,face,nodes):
    keywords=[];fragments=[];children=[];caps=set()
    registry=default_ability_fragment_registry()
    for node in nodes:
        if not node.exact or node.residual_ids:return None
        caps.update(node.capability_dependencies)
        if node.kind in {'triggered_ability','activated_ability','mana_ability'}:
            plan=attached_granted_ability_plan(node=node,quoted_text=node.text,oracle_id=record.oracle_id,face_id=face.face_id,source_line=node.span.line,card_name=record.name,keywords=record.keywords,node_id=node.node_id)
            if plan is None:return None
            fragments.append(dict(plan.fragment));children.append(replace(node,node_id=node.node_id+':backup-granted',kind=plan.node_kind,active_zone='battlefield',residual_ids=()))
        elif node.handlers:
            for descriptor in node.handlers:
                if registry.describe(str(descriptor.get('handler_id') or '')) is None:return None
                lowered=registry.lower(descriptor,None)
                if not lowered:return None
                fragments.extend(ability_fragment_to_dict(f) for f in lowered)
        elif node.kind=='keyword_ability':
            parsed=[k for k in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES if k.casefold() in {m.casefold() for m in node.mechanics}]
            if not parsed:return None
            keywords.extend(parsed)
        else:return None
    return tuple(dict.fromkeys(keywords)),tuple(fragments),tuple(children),caps


def compile_backup_faces(record,faces,*,material_line_for,capability_registry,capability_profile):
    result=[]
    for face in faces:
        nodes=list(face.nodes);residuals=list(face.residuals);children={};replacements={}
        for node in face.nodes:
            text=material_line_for(node.text).strip().rstrip('.')
            if not re.fullmatch(r'Backup [1-9]\d*(?:, backup [1-9]\d*)*',text,re.I):continue
            amounts=[int(n) for n in re.findall(r'backup (\d+)',text,re.I)]
            if any(n>10 for n in amounts):continue
            downstream=tuple(n for n in face.nodes if n.span.line>node.span.line)
            bundle=_bundle(record,face,downstream)
            if bundle is None:continue
            keywords,fragments,child_nodes,caps=bundle
            grant={'op':'apply_source_characteristics_until_end_of_turn','schema_version':7,'card':'$target.0','keywords':list(keywords),'ability_fragments':list(fragments),'exclude_source':True,'printed_only':True}
            from ..rules.temporary_ability_grant import decode_temporary_ability_grant
            try:decode_temporary_ability_grant(grant)
            except (TypeError,ValueError):continue
            caps.update({BACKUP_CAPABILITY,'counter.producer.fixed_effect','target.revalidate_resolution'})
            gate=explicit_capabilities_gate(tuple(sorted(caps)),capability_registry=capability_registry,capability_profile=capability_profile)
            removed=set(node.residual_ids)
            new_id=append_residual(residuals,kind='dependency_contract',text=node.text,span=node.span,reason='Backup requires complete downstream printed ability and shared target/counter/grant closure.',blockers=gate.blockers) if gate.blockers else None
            ids=(new_id,) if new_id else ()
            residuals=[r for r in residuals if r.residual_id not in removed]
            replacements[node.node_id]=tuple(OracleNode(node_id=node.node_id+f':backup:{i}',kind='triggered_ability',text=node.text,span=node.span,active_zone='battlefield',event='permanent.enter',lowerable=True,exact=not gate.blockers,
                template_id='printed-backup-ability-bundle-v1',effects=({'op':'place_counters','card':'$target.0','counter':'+1/+1','amount':amount,'source':'$source'},grant),
                target_schema={'zones':['battlefield'],'categories':['permanent'],'types_any':['creature'],'count':1},event_condition={'field':'card','op':'eq','value':'$source.ref'},
                mechanics=('backup','cr-603-handling-triggered-abilities','cr-115-targets','cr-122-counters','cr-611-continuous-effects'),runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,BACKUP_COVERAGE),
                residual_ids=ids,capability_dependencies=gate.capabilities,capability_closure=gate.closure.reachable if gate.closure else (),capability_profile=gate.closure.profile if gate.closure else None,capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
                for i,amount in enumerate(amounts,1))
            children.update({n.node_id:n for n in child_nodes})
        result.append(replace(face,nodes=tuple(n for old in nodes for n in replacements.get(old.node_id,(old,)))+tuple(children.values()),residuals=tuple(residuals)))
    return tuple(result)
