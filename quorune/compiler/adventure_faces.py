from __future__ import annotations

"""Bind the intrinsic Adventure lifecycle to the alternative face's source."""

from dataclasses import replace
from ..adventure import ADVENTURE_CAPABILITY,ADVENTURE_COVERAGE
from ..characteristic_evaluation import type_parts
from .dependency_gate import explicit_capabilities_gate
from .ir_model import append_residual


def compile_adventure_faces(record,faces,*,capability_registry,capability_profile):
    if record.layout!='adventure':return faces
    alternatives={str(face['name']) for face in record.faces
        if 'adventure' in type_parts(str(face.get('type_line') or ''))[1]}
    result=[]
    for face in faces:
        if face.face_name not in alternatives:result.append(face);continue
        residuals=list(face.residuals);nodes=[]
        for node in face.nodes:
            required=tuple(sorted({*node.capability_dependencies,ADVENTURE_CAPABILITY}))
            gate=explicit_capabilities_gate(required,capability_registry=capability_registry,capability_profile=capability_profile)
            residual_ids=node.residual_ids
            if gate.blockers:
                residual_ids=(*residual_ids,append_residual(residuals,kind='dependency_contract',text=node.text,
                    span=node.span,reason='The Adventure alternative requires its complete intrinsic lifecycle',blockers=gate.blockers))
            closure=gate.closure
            nodes.append(replace(node,exact=node.exact and not gate.blockers,
                capability_dependencies=gate.capabilities,capability_closure=closure.reachable if closure else (),
                capability_profile=closure.profile if closure else None,capability_fingerprint=closure.fingerprint if closure else None,
                residual_ids=residual_ids,runtime_coverage=tuple(sorted({*node.runtime_coverage,ADVENTURE_COVERAGE}))))
        result.append(replace(face,nodes=tuple(nodes),residuals=tuple(residuals)))
    return tuple(result)
