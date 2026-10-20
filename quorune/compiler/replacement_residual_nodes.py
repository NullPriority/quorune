from __future__ import annotations

"""Keep unowned replacement ordering residual after closed static dispatch."""

import re

from .ir_model import OracleNode,append_residual
from .kicked_self_replacement_templates import is_fixed_spell_self_replacement


_REPLACEMENT_MARKERS=re.compile(r'\b(instead|as .+ enters|enters .+ with|skip)\b',re.I)


def unowned_replacement_node(*,node_id,line,material_line,span,source_name,
        spell,permanent,has_faces,compile_effect,residuals):
    if not _REPLACEMENT_MARKERS.search(line):
        return None
    if spell and is_fixed_spell_self_replacement(material_line,source_name,
            compile_effect,has_faces=has_faces):
        return None
    residual_id=append_residual(residuals,kind='replacement_effect',text=line,span=span,
        reason='replacement/prevention ordering is not compiled',blockers=(
            'replacement applicability','affected-player ordering',
            'self-replacement and prevention ordering'))
    return OracleNode(node_id=node_id,kind='replacement_effect',text=line,span=span,
        active_zone='battlefield' if permanent else 'stack',event='replace',
        lowerable=False,exact=False,residual_ids=(residual_id,))
