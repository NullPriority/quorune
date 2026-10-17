from __future__ import annotations

"""Assemble two independently closed spell bodies under one paid text choice."""

from dataclasses import asdict
from ..spell_text_variants import SpellTextVariantKind
from .dependency_gate import dependency_gate
from .ir_model import OracleFaceIR, OracleNode, SourceSpan


def missing_paired_variant_residuals(ir, programs, kind):
    ability_ids = {program.ability_id for program in programs}
    return [{'face_id': face.face_id, 'residual_id': 'program_lowering:' + node.node_id,
        'kind': 'program_lowering', 'material': True, 'text': node.text, 'span': asdict(node.span),
        'reason': 'Recognized derived spell body has no executable correlated program',
        'blockers': ['complete paid spell variant lowering']}
        for face in ir.faces for node in face.nodes
        if node.kind == 'spell_variant' and kind.boundary in node.runtime_coverage
        and f'spell:{face.face_id}:{kind.value}' not in ability_ids]


def paired_spell_face(*, kind, face_id, face_name, oracle_text, material_rows, variants, spec,
        trusted_mechanics, capability_registry, capability_profile, residuals):
    if not isinstance(kind, SpellTextVariantKind) or len(variants) != 2:
        raise ValueError('Paired spell assembly requires one represented text variant')
    if any(not template or not effects or not mechanics for template, effects, targets, mechanics in variants):
        return None
    if any('modes' in schema for _, _, schema, _ in variants if schema is not None):
        return None
    gates = [dependency_gate(mechanics=mechanics, effects=effects, target_schema=targets,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, explicit_capabilities=(kind.capability,))
        for _, effects, targets, mechanics in variants]
    if any(gate.blockers or gate.closure is None or len(gate.capabilities) < 2 for gate in gates):
        return None
    span = SourceSpan(material_rows[0][2].start, material_rows[-1][2].end, material_rows[0][2].line)
    nodes = []
    for index, ((template, effects, targets, mechanics), gate) in enumerate(zip(variants, gates, strict=True)):
        nodes.append(OracleNode(node_id=f'{face_id}:n1' if index == 0 else f'{face_id}:{kind.value}',
            kind='spell_ability' if index == 0 else 'spell_variant', text=oracle_text, span=span,
            active_zone='stack', event='resolve', lowerable=True, exact=True,
            template_id=template, effects=effects, target_schema=targets,
            cost={kind.value: spec.to_dict()}, mechanics=tuple(dict.fromkeys((*mechanics, kind.value))),
            runtime_coverage=(kind.boundary, kind.normal_role if index == 0 else kind.paid_role),
            capability_dependencies=gate.capabilities, capability_closure=gate.closure.reachable,
            capability_profile=gate.closure.profile, capability_fingerprint=gate.closure.fingerprint))
    return OracleFaceIR(face_id=face_id, face_name=face_name, oracle_text=oracle_text,
        nodes=tuple(nodes), residuals=tuple(residuals))
