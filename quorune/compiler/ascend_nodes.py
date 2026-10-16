from __future__ import annotations

"""Exact keyword lowering retains the spell/permanent Ascend distinction."""

from ..city_blessing_model import ASCEND_STATIC_HANDLER,CITYS_BLESSING_CAPABILITY
from .dependency_gate import explicit_capability_gate
from .ir_model import OracleNode,append_residual
from .static_runtime_nodes import runtime_handler_node


def ascend_keyword_node(*,record,face_id,node_id,line,material_line,span,mechanics,
        capability_registry,capability_profile,residuals,**_unused):
    if mechanics!=('ascend',):return None
    faces=getattr(record,'faces',())
    face=next((f for f in faces if f.get('name')==face_id),None)
    type_line=str(face.get('type_line') or '') if face is not None else record.type_line if not faces else ''
    types=set(type_line.casefold().split(' — ')[0].split())
    spell=bool(types & {'instant','sorcery'})
    if material_line.strip().casefold().rstrip('.')!='ascend' or not types.intersection({'artifact','battle','creature','enchantment','land','planeswalker','instant','sorcery'}):
        rid=append_residual(residuals,kind='keyword_grammar',text=line,span=span,
            reason='Ascend requires its exact keyword and a known current face type.',blockers=('ordinary Ascend',))
        return OracleNode(node_id=node_id,kind='keyword_ability',text=line,span=span,active_zone='all',event='unresolved',
            lowerable=False,exact=False,template_id='ascend-residual-v1',mechanics=mechanics,residual_ids=(rid,))
    if not spell:
        return runtime_handler_node(node_id=node_id,line=line,span=span,
            compiled=('ascend-permanent-v1',{'handler_id':ASCEND_STATIC_HANDLER,'schema_version':1,'event':'characteristics.evaluate'},CITYS_BLESSING_CAPABILITY),
            kind='static_ability',event='characteristics.evaluate',capability_registry=capability_registry,
            capability_profile=capability_profile,residuals=residuals,
            dependency_reason='Ascend requires its canonical public designation owner.')
    gate=explicit_capability_gate(CITYS_BLESSING_CAPABILITY,capability_registry=capability_registry,capability_profile=capability_profile)
    ids=(append_residual(residuals,kind='dependency_contract',text=line,span=span,
        reason='Spell Ascend requires its canonical public designation owner.',blockers=gate.blockers),) if gate.blockers else ()
    return OracleNode(node_id=node_id,kind='spell_ability',text=line,span=span,active_zone='stack',event='resolve',
        lowerable=True,exact=not ids,template_id='ascend-spell-v1',mechanics=mechanics,
        effects=({'op':'ascend','player':'$controller'},),residual_ids=ids,capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure is not None else (),
        capability_profile=gate.closure.profile if gate.closure is not None else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure is not None else None)
