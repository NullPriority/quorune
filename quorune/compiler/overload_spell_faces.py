from __future__ import annotations

"""Compile both source-bound Overload bodies before admitting either one."""

import re
from dataclasses import asdict
from ..overload_model import (
    FixedManaOverloadSpec, OVERLOAD_CAPABILITY, OVERLOAD_FIELD, OVERLOAD_BOUNDARY,
    OVERLOAD_NORMAL, OVERLOAD_PAID, overload_body_fingerprint,
)
from ..util import mana_cost_to_vector
from .dependency_gate import dependency_gate
from .ir_model import OracleFaceIR, OracleNode, SourceSpan, append_residual


_COST = re.compile(r'^Overload (?P<cost>(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+)\.?$', re.I)


def missing_overload_variant_residuals(ir, programs):
    ability_ids = {program.ability_id for program in programs}
    return [{'face_id': face.face_id, 'residual_id': 'program_lowering:' + node.node_id,
        'kind': 'program_lowering', 'material': True, 'text': node.text, 'span': asdict(node.span),
        'reason': 'Recognized overloaded spell body has no executable correlated program',
        'blockers': ['complete overloaded spell variant lowering']}
        for face in ir.faces for node in face.nodes
        if node.kind == 'spell_variant' and OVERLOAD_BOUNDARY in node.runtime_coverage
        and f'spell:{face.face_id}:overload' not in ability_ids]


def residual_overload_keyword_node(*, node_id, line, span, residuals, **unused):
    """A declined whole-face pair must not become an inert trusted keyword."""
    rid = append_residual(residuals, kind='keyword_grammar', text=line, span=span,
        reason='Overload requires one fixed price and two independently closed correlated spell bodies',
        blockers=('unknown variants, incomplete bodies or missing cost/effect closure',))
    return OracleNode(node_id=node_id, kind='keyword_ability', text=line, span=span,
        active_zone='all', event='unresolved', lowerable=False, exact=False, residual_ids=(rid,))


def compile_fixed_overload_face(*, record, face_id, face_name, oracle_text, material_rows,
        effect_template, trusted_mechanics, capability_registry, capability_profile, residuals):
    if record.layout != 'normal' or record.faces or len(material_rows) != 2:
        return None
    costs = [row for row in material_rows if _COST.fullmatch(row[1]) is not None]
    if len(costs) != 1:
        return None
    match = _COST.fullmatch(costs[0][1])
    if match is None or re.search(r'\{[XYZ]\}', record.mana_cost or '', re.I):
        return None
    price, complex_symbols = mana_cost_to_vector(match['cost'].upper())
    if complex_symbols:
        return None
    body = next(row[1] for row in material_rows if row is not costs[0])
    if not re.search(r'\btarget\b', body, re.I):
        return None
    each_body = re.sub(r'\btarget\b', 'each', body, flags=re.I)
    variants = [effect_template(body, card_name=face_name or record.name),
        effect_template(each_body, card_name=face_name or record.name)]
    if any(not template or not effects or not mechanics for template, effects, targets, mechanics in variants):
        return None
    if variants[0][2] is None or variants[1][2] is not None:
        return None
    if any('modes' in schema for _, _, schema, _ in variants if schema is not None):
        return None
    gates = [dependency_gate(mechanics=mechanics, effects=effects, target_schema=targets,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, explicit_capabilities=(OVERLOAD_CAPABILITY,))
        for _, effects, targets, mechanics in variants]
    if any(gate.blockers or gate.closure is None or len(gate.capabilities) < 2 for gate in gates):
        return None
    spec = FixedManaOverloadSpec(f'{face_id}:overload:line:{costs[0][2].line}', price,
        overload_body_fingerprint(variants[0][1], variants[0][2]),
        overload_body_fingerprint(variants[1][1], variants[1][2]))
    span = SourceSpan(material_rows[0][2].start, material_rows[-1][2].end, material_rows[0][2].line)
    nodes = []
    for index, ((template, effects, targets, mechanics), gate) in enumerate(zip(variants, gates, strict=True)):
        nodes.append(OracleNode(node_id=f'{face_id}:n1' if index == 0 else f'{face_id}:overload',
            kind='spell_ability' if index == 0 else 'spell_variant', text=oracle_text, span=span,
            active_zone='stack', event='resolve', lowerable=True, exact=True,
            template_id=template, effects=effects, target_schema=targets,
            cost={OVERLOAD_FIELD: spec.to_dict()}, mechanics=tuple(dict.fromkeys((*mechanics, 'overload'))),
            runtime_coverage=(OVERLOAD_BOUNDARY, OVERLOAD_NORMAL if index == 0 else OVERLOAD_PAID),
            capability_dependencies=gate.capabilities, capability_closure=gate.closure.reachable,
            capability_profile=gate.closure.profile, capability_fingerprint=gate.closure.fingerprint))
    return OracleFaceIR(face_id=face_id, face_name=face_name, oracle_text=oracle_text,
        nodes=tuple(nodes), residuals=tuple(residuals))
