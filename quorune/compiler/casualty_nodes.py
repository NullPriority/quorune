"""Source-spanned fixed Casualty declaration, independently of the spell body."""
import re
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..characteristic_evaluation import type_parts
from ..casualty_model import FixedCasualtySpec, CASUALTY_CAPABILITY, CASUALTY_HANDLER
from .ir_model import OracleNode, append_residual
from .static_runtime_nodes import runtime_handler_node


def printed_casualty_spec(record):
    types, _, _ = type_parts(record.type_line)
    lines = [(i, row.strip()) for i, row in enumerate(record.oracle_text.splitlines()) if re.match(r'^Casualty\b', row.strip(), re.I)]
    if (record.layout != 'normal' or record.faces or not {'instant', 'sorcery'} & types or len(lines) != 1
            or re.search(r'^(?:Convoke|Improvise|Delve|Emerge|Bargain|Conspire|Kicker|Multikicker|Replicate|As an additional cost)\b', record.oracle_text, re.I | re.M)):
        return None
    index, row = lines[0]
    match = re.fullmatch(r'Casualty (?P<n>\d+)(?:\s+\([^()]*\))?\.?', row, re.I)
    return FixedCasualtySpec(f'front:casualty:line:{index + 1}', int(match['n'])) if match else None


def casualty_static_node(*, record, node_id, line, material_line, span, capability_registry, capability_profile, residuals):
    spec = printed_casualty_spec(record)
    if spec is None:
        rid = append_residual(residuals, kind='keyword_grammar', text=line, span=span,
            reason='Casualty requires one printed fixed threshold on an independently complete instant or sorcery',
            blockers=('multiple/granted instances, permanent/variable-copy exceptions and mixed consuming costs',))
        return OracleNode(node_id=node_id, kind='keyword_ability', text=line, span=span, active_zone='all', event='unresolved', lowerable=False, exact=False, residual_ids=(rid,))
    descriptor = {'handler_id': CASUALTY_HANDLER, 'schema_version': 1, 'event': 'cast.cost',
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True, 'casualty': spec.to_dict()}
    return runtime_handler_node(node_id=node_id, line=line, span=span,
        compiled=('ordinary-fixed-casualty-v1', descriptor, (CASUALTY_CAPABILITY,)), kind='keyword_ability',
        event='cast.cost', active_zone='all', dependency_reason='Casualty needs power-qualified sacrifice and one independent cast-copy trigger',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
