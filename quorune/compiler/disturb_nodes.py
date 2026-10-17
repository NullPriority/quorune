from __future__ import annotations

"""Source-spanned fixed Disturb authority from a transforming front face."""

import re
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..disturb_model import DISTURB_CAPABILITY, DISTURB_HANDLER, FixedManaDisturbSpec
from ..util import mana_cost_to_vector
from .ir_model import OracleNode, append_residual
from .static_runtime_nodes import runtime_handler_node


_COST = re.compile(r'^Disturb (?P<cost>(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+)'
    r'(?:\s+\([^()]*\))?\.?$', re.I)


def printed_disturb_spec(record):
    if record.layout != 'transform' or len(record.faces) != 2:
        return None
    front, back = record.faces
    lines = [(i, line.strip()) for i, line in enumerate(front.get('oracle_text', '').splitlines())
        if re.match(r'^Disturb\b', line.strip(), re.I)]
    if len(lines) != 1 or any(re.match(r'^Disturb\b', line.strip(), re.I)
            for line in back.get('oracle_text', '').splitlines()):
        return None
    index, line = lines[0]
    match = _COST.fullmatch(line)
    if match is None:
        return None
    mana, complex_symbols = mana_cost_to_vector(match['cost'].upper())
    if complex_symbols:
        return None
    return FixedManaDisturbSpec(f'front:disturb:line:{index + 1}', str(front['name']), str(back['name']), mana)


def disturb_static_node(*, record, face_name, node_id, line, material_line, span,
        capability_registry, capability_profile, residuals):
    if re.match(r'^Disturb\b', material_line, re.I) is None:
        return None
    spec = printed_disturb_spec(record)
    if spec is None or face_name != spec.front_face:
        rid = append_residual(residuals, kind='keyword_grammar', text=line, span=span,
            reason='Disturb requires one fixed ordinary mana front-face declaration on a transforming card',
            blockers=('complex costs, repeated declarations and other source faces',))
        return OracleNode(node_id=node_id, kind='keyword_ability', text=line, span=span, active_zone='all',
            event='unresolved', lowerable=False, exact=False, residual_ids=(rid,))
    descriptor = {'handler_id': DISTURB_HANDLER, 'schema_version': 1, 'event': 'cast.cost',
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True, 'disturb': spec.to_dict()}
    return runtime_handler_node(node_id=node_id, line=line, span=span,
        compiled=('fixed-mana-disturb-v1', descriptor, (DISTURB_CAPABILITY,)), kind='keyword_ability',
        event='cast.cost', active_zone='all', dependency_reason='Disturb requires complete front/back authority',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
