from __future__ import annotations

"""Derive ordinary and bracket-removed Cleave bodies at the compiler boundary."""

import re
from .ir_model import OracleNode, append_residual
from .paired_spell_faces import paired_spell_face
from ..cleave_model import FixedManaCleaveSpec
from ..spell_text_variants import SpellTextVariantKind, spell_body_fingerprint
from ..util import mana_cost_to_vector


_COST = re.compile(r'^Cleave (?P<cost>(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+)\.?$', re.I)


def residual_cleave_keyword_node(*, node_id, line, span, residuals, **unused):
    rid = append_residual(residuals, kind='keyword_grammar', text=line, span=span,
        reason='Cleave requires one fixed price and two independently closed bracket-derived spell bodies',
        blockers=('unsupported brackets, prices, source forms or incomplete paired bodies',))
    return OracleNode(node_id=node_id, kind='keyword_ability', text=line, span=span,
        active_zone='all', event='unresolved', lowerable=False, exact=False, residual_ids=(rid,))


def cleave_bodies(body):
    """Reject malformed or nested brackets instead of guessing their extent."""
    if not re.search(r'\[[^\[\]]+\]', body):
        return None
    removed = re.sub(r'\[[^\[\]]+\]', '', body)
    if '[' in removed or ']' in removed:
        return None
    normal = body.replace('[', '').replace(']', '')
    paid = re.sub(r' +', ' ', removed)
    paid = re.sub(r'\s+([,.])', r'\1', paid)
    paid = re.sub(r',\s*,', ',', paid)
    return normal.strip(), paid.strip()


def compile_fixed_cleave_face(*, record, face_id, face_name, oracle_text, material_rows,
        effect_template, trusted_mechanics, capability_registry, capability_profile, residuals):
    if record.layout != 'normal' or record.faces or len(material_rows) < 2:
        return None
    costs = [row for row in material_rows if _COST.fullmatch(row[1]) is not None]
    if len(costs) != 1 or re.search(r'\{[XYZ]\}', record.mana_cost or '', re.I):
        return None
    price, complex_symbols = mana_cost_to_vector(_COST.fullmatch(costs[0][1])['cost'].upper())
    if complex_symbols:
        return None
    bodies = cleave_bodies(' '.join(row[1] for row in material_rows if row is not costs[0]))
    if bodies is None:
        return None
    variants = [effect_template(body, card_name=face_name or record.name) for body in bodies]
    if any(not template or not effects or not mechanics for template, effects, schema, mechanics in variants):
        return None
    try:
        spec = FixedManaCleaveSpec(f'{face_id}:cleave:line:{costs[0][2].line}', price,
            spell_body_fingerprint(variants[0][1], variants[0][2]), spell_body_fingerprint(variants[1][1], variants[1][2]))
    except ValueError:
        return None
    return paired_spell_face(kind=SpellTextVariantKind.CLEAVE, face_id=face_id, face_name=face_name,
        oracle_text=oracle_text, material_rows=material_rows, variants=variants, spec=spec,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals)
