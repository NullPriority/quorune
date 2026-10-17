from __future__ import annotations

"""Compile both source-bound Overload bodies before admitting either one."""

import re
from dataclasses import asdict
from ..overload_model import (
    FixedManaOverloadSpec, OVERLOAD_CAPABILITY, OVERLOAD_FIELD, OVERLOAD_BOUNDARY,
    OVERLOAD_NORMAL, OVERLOAD_PAID, overload_body_fingerprint,
)
from ..util import mana_cost_to_vector
from .paired_spell_faces import paired_spell_face
from ..spell_text_variants import SpellTextVariantKind
from .ir_model import OracleFaceIR, OracleNode, SourceSpan, append_residual


_COST = re.compile(r'^Overload (?P<cost>(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+)\.?$', re.I)


def missing_overload_variant_residuals(ir, programs):
    from .paired_spell_faces import missing_paired_variant_residuals
    return missing_paired_variant_residuals(ir, programs, SpellTextVariantKind.OVERLOAD)


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
    spec = FixedManaOverloadSpec(f'{face_id}:overload:line:{costs[0][2].line}', price,
        overload_body_fingerprint(variants[0][1], variants[0][2]),
        overload_body_fingerprint(variants[1][1], variants[1][2]))
    return paired_spell_face(kind=SpellTextVariantKind.OVERLOAD, face_id=face_id, face_name=face_name,
        oracle_text=oracle_text, material_rows=material_rows, variants=variants, spec=spec,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals)
