from __future__ import annotations

"""Link a printed fixed Multikicker instance to its named entry counters."""

import re

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..multikicker_model import (
    FixedManaMultikickerSpec, MultikickerEntrySpec, MULTIKICKER_CAPABILITY,
    MULTIKICKER_ENTRY_CAPABILITY, MULTIKICKER_COST_HANDLER, MULTIKICKER_ENTRY_HANDLER,
)
from ..util import mana_cost_to_vector
from .ir_model import OracleNode, append_residual
from .static_runtime_nodes import runtime_handler_node


_COST = re.compile(r'^Multikicker (?P<cost>(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+)\.?$', re.I)
_ENTRY = re.compile(r'^This (?:artifact|creature|permanent) enters with a (?P<counter>\+1/\+1|charge) counter on it for each time it was kicked\.$', re.I)


def _printed_cost(record):
    lines = [(index, line.strip()) for index, line in enumerate(record.oracle_text.splitlines())
        if re.match(r'^(?:Multikicker|Kicker|Squad|Offspring)\b', line.strip(), re.I)]
    if record.layout != 'normal' or record.faces or len(lines) != 1:
        return None
    index, oracle_line = lines[0]
    material = re.sub(r'\s*\([^()]*\)\s*$', '', oracle_line).strip()
    match = _COST.fullmatch(material)
    if match is None:
        return None
    requirements, complex_symbols = mana_cost_to_vector(match['cost'].upper())
    if complex_symbols:
        return None
    try:
        return FixedManaMultikickerSpec(f'front:multikicker:line:{index+1}', requirements)
    except ValueError:
        return None


def multikicker_static_node(*, record, node_id, line, material_line, span,
        capability_registry, capability_profile, residuals):
    cost_line = bool(re.match(r'^Multikicker\b', material_line, re.I))
    entry = _ENTRY.fullmatch(material_line.strip())
    if not cost_line and entry is None:
        return None
    spec = _printed_cost(record)
    if spec is None:
        rid = append_residual(residuals, kind='keyword_grammar', text=line, span=span,
            reason='Multikicker needs one linked positive fixed ordinary-mana printed instance',
            blockers=('multiple, variable, nonmana and unlinked Multikicker instances',))
        return OracleNode(node_id=node_id, kind='keyword_ability' if cost_line else 'replacement_effect',
            text=line, span=span, active_zone='all', event='unresolved', lowerable=False,
            exact=False, residual_ids=(rid,))
    if cost_line:
        descriptor = {'handler_id': MULTIKICKER_COST_HANDLER, 'schema_version': 1,
            'event': 'cast.cost', REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True, 'multikicker': spec.to_dict()}
        capability = MULTIKICKER_CAPABILITY
    else:
        descriptor = {'handler_id': MULTIKICKER_ENTRY_HANDLER, 'schema_version': 1,
            'event': 'zone.change', 'entry': MultikickerEntrySpec(spec, entry['counter'].casefold()).to_dict()}
        capability = MULTIKICKER_ENTRY_CAPABILITY
    return runtime_handler_node(node_id=node_id, line=line, span=span,
        compiled=('fixed-mana-multikicker-v1' if cost_line else 'multikicker-named-entry-counters-v1', descriptor, (capability,)),
        kind='keyword_ability' if cost_line else 'replacement_effect', event=descriptor['event'],
        active_zone='all', runtime_coverage=('multikicker_linked_count',),
        dependency_reason='Multikicker needs complete payment and linked entry counter ownership',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
