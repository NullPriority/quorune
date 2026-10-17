from __future__ import annotations

"""Source-spanned paired cost/cast-trigger authority for fixed Replicate."""

import re
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..characteristic_evaluation import type_parts
from ..replicate_model import FixedManaReplicateSpec, REPLICATE_CAPABILITY, REPLICATE_HANDLER
from ..util import mana_cost_to_vector
from .ir_model import OracleNode, append_residual
from .static_runtime_nodes import runtime_handler_node


_COST = re.compile(r'^Replicate (?P<cost>(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+)(?:\s+\([^()]*\))?\.?$', re.I)


def printed_replicate_spec(record):
    types, _, _ = type_parts(record.type_line)
    lines = [(i, line.strip()) for i, line in enumerate(record.oracle_text.splitlines())
        if re.match(r'^Replicate\b', line.strip(), re.I)]
    if record.layout != 'normal' or record.faces or not {'instant', 'sorcery'} & types or len(lines) != 1:
        return None
    index, line = lines[0]
    match = _COST.fullmatch(line)
    if match is None:
        return None
    requirements, complex_symbols = mana_cost_to_vector(match['cost'].upper())
    if complex_symbols:
        return None
    try:
        return FixedManaReplicateSpec(f'front:replicate:line:{index + 1}', requirements)
    except ValueError:
        return None


def replicate_static_node(*, record, node_id, line, material_line, span,
        capability_registry, capability_profile, residuals):
    if re.match(r'^Replicate\b', material_line, re.I) is None:
        return None
    spec = printed_replicate_spec(record)
    if spec is None:
        rid = append_residual(residuals, kind='keyword_grammar', text=line, span=span,
            reason='Replicate requires one positive fixed ordinary-mana instance on a normal instant or sorcery',
            blockers=('variable/nonmana/repeated/granted instances and permanent spells',))
        return OracleNode(node_id=node_id, kind='keyword_ability', text=line, span=span, active_zone='all',
            event='unresolved', lowerable=False, exact=False, residual_ids=(rid,))
    descriptor = {'handler_id': REPLICATE_HANDLER, 'schema_version': 1, 'event': 'cast.cost',
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True, 'replicate': spec.to_dict()}
    return runtime_handler_node(node_id=node_id, line=line, span=span,
        compiled=('fixed-mana-replicate-v1', descriptor, (REPLICATE_CAPABILITY,)), kind='keyword_ability',
        event='cast.cost', active_zone='all', dependency_reason='Replicate requires complete paired cost and copy-trigger authority',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
