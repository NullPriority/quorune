from __future__ import annotations

"""Source-spanned paired cost and copy-trigger authority for Conspire."""

import re
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..characteristic_evaluation import type_parts
from ..conspire_model import FixedConspireSpec, CONSPIRE_CAPABILITY, CONSPIRE_HANDLER
from .ir_model import OracleNode, append_residual
from .static_runtime_nodes import runtime_handler_node


def printed_conspire_spec(record):
    types, _, _ = type_parts(record.type_line)
    lines = [(i, line.strip()) for i, line in enumerate(record.oracle_text.splitlines())
        if re.match(r'^Conspire\b', line.strip(), re.I)]
    if (record.layout != 'normal' or record.faces or not {'instant', 'sorcery'} & types or len(lines) != 1
            or re.search(r'\{[XYZ]\}', record.mana_cost or '', re.I)
            or re.search(r'^(?:Convoke|Improvise|Delve|Emerge|Bargain|Kicker|Multikicker|Replicate|As an additional cost)\b',
                record.oracle_text, re.I | re.M)):
        return None
    index, line = lines[0]
    if re.fullmatch(r'Conspire(?:\s+\([^()]*\))?\.?', line, re.I) is None:
        return None
    return FixedConspireSpec(f'front:conspire:line:{index + 1}')


def conspire_static_node(*, record, node_id, line, material_line, span,
        capability_registry, capability_profile, residuals):
    if re.match(r'^Conspire\b', material_line, re.I) is None:
        return None
    spec = printed_conspire_spec(record)
    if spec is None:
        rid = append_residual(residuals, kind='keyword_grammar', text=line, span=span,
            reason='Conspire requires one ordinary printed instance with independently represented fixed payment',
            blockers=('multiple/granted instances, variable bases, permanent spells and mixed consuming costs',))
        return OracleNode(node_id=node_id, kind='keyword_ability', text=line, span=span, active_zone='all',
            event='unresolved', lowerable=False, exact=False, residual_ids=(rid,))
    descriptor = {'handler_id': CONSPIRE_HANDLER, 'schema_version': 1, 'event': 'cast.cost',
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True, 'conspire': spec.to_dict()}
    return runtime_handler_node(node_id=node_id, line=line, span=span,
        compiled=('ordinary-conspire-v1', descriptor, (CONSPIRE_CAPABILITY,)), kind='keyword_ability',
        event='cast.cost', active_zone='all', dependency_reason='Conspire requires complete tap-payment and copy-trigger authority',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
