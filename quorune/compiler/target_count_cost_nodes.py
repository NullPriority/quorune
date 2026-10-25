from __future__ import annotations

"""Complete source sentences define mandatory per-extra-target mana prices."""

import re

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..repeated_mana_costs import FixedRepeatedManaCostSpec
from ..target_count_cost_model import FixedManaPerTargetCostSpec, TARGET_COUNT_COST_CAPABILITY, TARGET_COUNT_COST_HANDLER
from ..util import mana_cost_to_vector
from .static_runtime_nodes import runtime_handler_node

_COST = re.compile(r'(?:Strive — )?This spell costs (?P<cost>(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+) more to cast for each target beyond the first\.', re.I)


def target_count_cost_static_node(*, record, node_id, line, material_line, span,
        capability_registry, capability_profile, residuals):
    match = _COST.fullmatch(material_line.strip())
    source_lines = [text.strip() for text in record.oracle_text.splitlines() if _COST.fullmatch(text.strip())]
    if (match is None or record.layout != 'normal' or record.faces or len(source_lines) != 1
            or len(re.findall(r'\bany number of (?:other )?target\b',record.oracle_text,re.I)) != 1):
        return None
    requirements, complex_symbols = mana_cost_to_vector(match['cost'].upper())
    if complex_symbols or not any(requirements.values()):return None
    spec = FixedManaPerTargetCostSpec(FixedRepeatedManaCostSpec('front:target-count-cost', requirements))
    descriptor = {'handler_id': TARGET_COUNT_COST_HANDLER, 'schema_version': 1,
        'event': 'cast.cost', REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True, 'target_cost': spec.to_dict()}
    return runtime_handler_node(node_id=node_id, line=line, span=span,
        compiled=('fixed-mana-per-extra-target-v1', descriptor, (TARGET_COUNT_COST_CAPABILITY,)),
        kind='static_ability', event='cast.cost', active_zone='all', runtime_coverage=('target-count-additional-cost',),
        dependency_reason='Mandatory additional mana is computed from all originally chosen targets before cost locking',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
