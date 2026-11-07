from __future__ import annotations

"""Keep unowned replacement ordering residual after closed static dispatch."""

import re

from ..damage_death_exile_model import DAMAGE_DEATH_EXILE_MECHANIC
from ..turn_skip_model import TURN_SKIP_MECHANIC
from .ir_model import OracleNode,append_residual
from .kicked_self_replacement_templates import is_fixed_spell_self_replacement


_REPLACEMENT_MARKERS=re.compile(r'\b(instead|as .+ enters|enters .+ with|skip)\b',re.I)


def _balanced_reminder_delimiters(line):
    depth=0
    for character in line:
        if character=='(':depth+=1
        elif character==')':
            if not depth:return False
            depth-=1
    return depth==0


def unowned_replacement_node(*,node_id,line,material_line,span,source_name,
        spell,permanent,has_faces,compile_effect,residuals,bargain_available=False):
    if not _REPLACEMENT_MARKERS.search(material_line):
        if not _REPLACEMENT_MARKERS.search(line) or _balanced_reminder_delimiters(line):
            return None
    if spell and bargain_available and compile_effect(material_line,card_name=source_name,bargain_available=True)[0]=='linked-bargain-same-target-v1':
        return None
    if spell and is_fixed_spell_self_replacement(material_line,source_name,
            compile_effect,has_faces=has_faces):
        return None
    if spell and re.search(r"\bskip\b", material_line, re.I):
        if TURN_SKIP_MECHANIC in compile_effect(material_line, card_name=source_name)[3]:
            return None
    if 'would die this turn, exile it instead.' in material_line.casefold():
        body = material_line if spell else material_line.partition(': ')[2]
        if body and DAMAGE_DEATH_EXILE_MECHANIC in compile_effect(body, card_name=source_name)[3]:
            return None
    residual_id=append_residual(residuals,kind='replacement_effect',text=line,span=span,
        reason='replacement/prevention ordering is not compiled',blockers=(
            'replacement applicability','affected-player ordering',
            'self-replacement and prevention ordering'))
    return OracleNode(node_id=node_id,kind='replacement_effect',text=line,span=span,
        active_zone='battlefield' if permanent else 'stack',event='replace',
        lowerable=False,exact=False,residual_ids=(residual_id,))
