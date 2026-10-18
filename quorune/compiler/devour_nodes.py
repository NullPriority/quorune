from __future__ import annotations

"""Closed source-spanned Devour keyword productions."""

import re
from ..devour import DevourSpec,DevourQuality,DEVOUR_HANDLER,DEVOUR_CAPABILITY
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode,SourceSpan,append_residual


def _parts(material):
    if not re.match(r'^devour\b',material,re.I):return None
    result=[]
    for text in re.split(r',\s*(?=devour\b)',material.strip().rstrip('.'),flags=re.I):
        fixed=re.fullmatch(r'Devour(?: (?P<quality>artifact|Food))? (?P<count>\d+)',text,re.I)
        if fixed is not None:spec=DevourSpec(DevourQuality((fixed['quality'] or 'creature').casefold()),int(fixed['count']))
        elif re.fullmatch(r'Devour X, where X is the number of creatures devoured this way',text,re.I):spec=DevourSpec(DevourQuality.CREATURE,'devoured_count')
        else:return None
        result.append((text,spec))
    return result


def devour_keyword_nodes(*,node_id,line,material_line,span,capability_registry,capability_profile,residuals):
    parts=_parts(material_line)
    if parts is None:return None
    gate=explicit_capabilities_gate((DEVOUR_CAPABILITY,'counter.placement.quantity_replacement'),
        capability_registry=capability_registry,capability_profile=capability_profile)
    nodes=[];cursor=0
    for index,(text,spec) in enumerate(parts):
        if len(parts)==1:source,part_span=line,span
        else:
            match=re.search(r'\s+'.join(map(re.escape,text.split())),line[cursor:],re.I)
            if match is None:return None
            start=cursor+match.start();end=cursor+match.end();cursor=end
            source=line[start:end];part_span=SourceSpan(span.start+start,span.start+end,span.line)
        rid=() if not gate.blockers else (append_residual(residuals,kind='dependency_contract',text=source,span=part_span,
            reason='Devour requires its canonical combined-entry and counter capabilities',blockers=gate.blockers),)
        nodes.append(OracleNode(node_id=node_id+('' if len(parts)==1 else f':devour:{index}'),kind='static_ability',text=source,span=part_span,
            active_zone='all',event='zone.change',lowerable=True,exact=not gate.blockers,template_id='devour-entry-v1',
            handlers=({'handler_id':DEVOUR_HANDLER,'schema_version':1,'event':'zone.change','devour':spec.to_dict()},),
            runtime_coverage=('typed_devour_entry',CURRENT_ABILITY_FRAGMENT_COVERAGE),residual_ids=rid,capability_dependencies=gate.capabilities,
            capability_closure=gate.closure.reachable if gate.closure else (),capability_profile=gate.closure.profile if gate.closure else None,
            capability_fingerprint=gate.closure.fingerprint if gate.closure else None))
    return tuple(nodes)
