from __future__ import annotations

"""One source-spanned ordinary mana Emerge declaration on a creature card."""

import re
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..emerge_model import EMERGE_CAPABILITY, EMERGE_HANDLER, FixedManaEmergeSpec
from ..util import mana_cost_to_vector
from .ir_model import OracleNode, append_residual
from .static_runtime_nodes import runtime_handler_node


_COST = re.compile(r'^Emerge (?P<cost>(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+)'
    r'(?:\s+\([^()]*\))?\.?$', re.I)


def printed_emerge_spec(record):
    lines = [(i, line.strip()) for i, line in enumerate(record.oracle_text.splitlines())
        if re.match(r'^Emerge\b', line.strip(), re.I)]
    if (record.layout != 'normal' or record.faces or len(lines) != 1
            or 'creature' not in record.type_line.replace('—', '-').split('-', 1)[0].casefold().split()):
        return None
    index, line = lines[0]
    match = _COST.fullmatch(line)
    if match is None:
        return None
    mana, complex_symbols = mana_cost_to_vector(match['cost'].upper())
    if complex_symbols:
        return None
    return FixedManaEmergeSpec(f'front:emerge:line:{index + 1}', mana)


def emerge_static_node(*, record, node_id, line, material_line, span,
        capability_registry, capability_profile, residuals):
    if re.match(r'^Emerge\b', material_line, re.I) is None:
        return None
    spec = printed_emerge_spec(record)
    if spec is None:
        rid = append_residual(residuals, kind='keyword_grammar', text=line, span=span,
            reason='Emerge requires one fixed ordinary mana declaration on a normal creature card',
            blockers=('complex Emerge costs, qualified sacrifices, multiple instances and other card forms',))
        return OracleNode(node_id=node_id, kind='keyword_ability', text=line, span=span,
            active_zone='all', event='unresolved', lowerable=False, exact=False, residual_ids=(rid,))
    descriptor = {'handler_id': EMERGE_HANDLER, 'schema_version': 1, 'event': 'cast.cost',
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True, 'emerge': spec.to_dict()}
    return runtime_handler_node(node_id=node_id, line=line, span=span,
        compiled=('fixed-mana-emerge-v1', descriptor, (EMERGE_CAPABILITY,)),
        kind='keyword_ability', event='cast.cost', active_zone='all', runtime_coverage=('fixed_mana_emerge',),
        dependency_reason='Emerge requires complete alternative cost, chosen sacrifice and total reduction ownership',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
