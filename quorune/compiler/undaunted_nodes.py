"""Source-spanned Undaunted instances reuse the canonical self-cost owner."""
from __future__ import annotations

import re
from .cast_cost_modifier_templates import self_spell_cost_reduction_handler
from .static_runtime_nodes import runtime_handler_node

UNDAUNTED_CAPABILITY = 'casting.cost.undaunted'


def undaunted_keyword_node(*, node_id, line, material_line, span, mechanics,
                          capability_registry, capability_profile, residuals, **unused):
    if (not mechanics or set(mechanics) != {'undaunted'}
            or re.fullmatch(r'Undaunted(?:\s*,\s*Undaunted)*\.?', material_line, re.I) is None):
        return None
    template, descriptor, capability = self_spell_cost_reduction_handler(
        'This spell costs {1} less to cast for each opponent you have.')
    terms = descriptor['reduction']['terms']
    descriptor = {**descriptor, 'reduction':{**descriptor['reduction'], 'terms':terms * len(mechanics)}}
    return runtime_handler_node(node_id=node_id, line=line, span=span,
        compiled=(template + ':undaunted', descriptor, (capability, UNDAUNTED_CAPABILITY)),
        kind='keyword_ability', event=descriptor['event'], active_zone='all',
        dependency_reason='Undaunted requires exact active-opponent counts and ordinary total-cost authority',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
