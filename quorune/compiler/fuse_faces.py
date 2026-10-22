from __future__ import annotations

"""Bind a source-wide Fuse method only after both physical bodies lower."""

from dataclasses import replace
from ..rules.fuse_model import FuseHalf, FuseSpec, FUSE_BOUNDARY, FUSE_HANDLER, FUSE_CAPABILITY
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .static_runtime_nodes import runtime_handler_node


def compile_fuse_faces(record, faces, *, material_line_for, capability_registry, capability_profile):
    if record.layout != 'split' or len(record.faces) != 2 or len(faces) != 2:
        return faces
    fuse_nodes = [tuple(n for n in face.nodes if material_line_for(n.text).strip() == 'Fuse') for face in faces]
    if any(len(nodes) != 1 for nodes in fuse_nodes):
        return faces
    cleaned = []
    for face, markers in zip(faces, fuse_nodes, strict=True):
        marker = markers[0]
        residuals = tuple(r for r in face.residuals if r.residual_id not in marker.residual_ids)
        nodes = tuple(n for n in face.nodes if n is not marker)
        if (any(r.material for r in residuals) or not nodes or any(not n.exact or n.kind != 'spell_ability'
                or n.handlers or n.cost or n.event_condition for n in nodes)):
            return faces
        cleaned.append(replace(face, nodes=nodes, residuals=residuals))
    try:
        from ..rules.face_characteristic_values import printed_face_colors
        spec = FuseSpec(*(FuseHalf(face.face_id, str(raw['name']), str(raw['mana_cost']),
            str(raw['type_line']), tuple(printed_face_colors(raw)))
            for face, raw in zip(faces, record.faces, strict=True)))
    except (KeyError, TypeError, ValueError):
        return faces
    marker = fuse_nodes[0][0]
    residuals = list(cleaned[0].residuals)
    declaration = runtime_handler_node(node_id=marker.node_id, line=marker.text, span=marker.span,
        compiled=('split-fuse-declaration-v1', {'handler_id': FUSE_HANDLER, 'schema_version': 1,
            'event': 'characteristics.evaluate', 'fuse': spec.to_dict(), REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True}, FUSE_CAPABILITY),
        kind='static_ability', event='characteristics.evaluate', active_zone='all',
        runtime_coverage=(FUSE_BOUNDARY,), dependency_reason='Fuse requires complete hand casting and ordered two-body resolution.',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
    if not declaration.exact:
        failures=tuple(residuals[len(cleaned[0].residuals):])
        return (replace(faces[0],residuals=(*faces[0].residuals,*failures)),faces[1])
    result = []
    for index, face in enumerate(cleaned):
        nodes = []
        for node in face.nodes:
            dependencies = tuple(sorted({*node.capability_dependencies, FUSE_CAPABILITY}))
            closure = capability_registry.closure(dependencies, profile=capability_profile) if capability_registry else None
            if closure is None or not closure.trusted:
                return faces
            nodes.append(replace(node,
                capability_dependencies=dependencies, capability_closure=closure.reachable,
                capability_profile=closure.profile, capability_fingerprint=closure.fingerprint))
        if index == 0:
            nodes.append(declaration)
        result.append(replace(face, nodes=tuple(nodes), residuals=tuple(residuals) if index == 0 else face.residuals))
    return tuple(result)


def missing_fuse_program_residuals(ir, programs):
    ids = {program.ability_id for program in programs}
    result = []
    for face in ir.faces:
        for node in face.nodes:
            for handler in node.handlers:
                if handler.get('handler_id') != FUSE_HANDLER:
                    continue
                spec = FuseSpec.from_dict(handler['fuse'])
                if spec.ability_id not in ids:
                    result.append({'face_id': face.face_id, 'residual_id': 'program_lowering:' + node.node_id,
                        'kind': 'program_lowering', 'material': True, 'text': node.text,
                        'span': {'start': node.span.start, 'end': node.span.end, 'line': node.span.line},
                        'reason': 'Fuse declaration has no complete correlated executable body',
                        'blockers': ['complete fused spell program lowering']})
    return result
