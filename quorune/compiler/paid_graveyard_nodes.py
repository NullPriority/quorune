from __future__ import annotations

"""Bind complete ordinary source cards to typed intrinsic graveyard prices."""

import re

from ..additional_cost_vocabulary import DISCARD_ONE_COST
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
from ..rules.graveyard_cast_costs import PaidGraveyardCastSpec
from .paid_graveyard_templates import paid_graveyard_template
from .static_runtime_nodes import runtime_handler_node


def paid_graveyard_node(*, record, node_id, line, material_line, span,
        capability_registry, capability_profile, residuals):
    if record.faces or record.layout != 'normal' or re.fullmatch(
            r'(?:\{(?:[0-9]+|[WUBRGC])\})+', record.mana_cost or '') is None:
        return None
    if re.search(r'\bAs an additional cost\b', record.oracle_text, re.I):
        return None
    compiled = paid_graveyard_template(material_line)
    if compiled is None:
        return None
    spec = PaidGraveyardCastSpec.from_dict(compiled[1]['payment'])
    if (spec.mana_cost is None and len(spec.additional_costs) == 1
            and spec.additional_costs[0].get('operation') == DISCARD_ONE_COST):
        return None  # Keep the already-versioned fixed-discard production and wire.
    labels = {material_line.split(' — ', 1)[0].casefold()} if ' — ' in material_line else set()
    ordinary = {str(k).casefold() for k in FIXED_CHARACTERISTIC_KEYWORDS} | {'enchant', 'flash'} | labels
    if {str(k).casefold() for k in record.keywords} - ordinary:
        return None
    return runtime_handler_node(node_id=node_id, line=line, span=span,
        compiled=compiled, kind='static_ability', event='cast.zone.permission', active_zone='all',
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
        dependency_reason='Intrinsic paid graveyard casting requires its current zone and complete payment owners')
