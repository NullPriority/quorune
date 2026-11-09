from __future__ import annotations

"""Keep unowned replacement ordering residual after closed static dispatch."""

import re

from ..damage_death_exile_model import DAMAGE_DEATH_EXILE_MECHANIC
from ..turn_skip_model import TURN_SKIP_MECHANIC
from .bargain_nodes import printed_bargain_spec
from .ir_model import OracleNode,append_residual
from .kicked_self_replacement_templates import is_fixed_spell_self_replacement
from .oracle_source_text import without_ability_word_label
from .reanimation_followup_templates import fixed_reanimation_followup_template


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
        spell,permanent,record,oracle_text,compile_effect,residuals):
    if not _REPLACEMENT_MARKERS.search(material_line):
        if not _REPLACEMENT_MARKERS.search(line) or _balanced_reminder_delimiters(line):
            return None
    if spell and printed_bargain_spec(record) is not None and compile_effect(material_line,card_name=source_name,bargain_available=True)[0]=='linked-bargain-same-target-v1':
        return None
    if spell and is_fixed_spell_self_replacement(material_line,source_name,
            compile_effect,has_faces=bool(record.faces)):
        return None
    if spell and " damage divided as you choose among " in material_line.casefold():
        from .announced_damage_templates import VARIABLE_DAMAGE_TEMPLATE_ID
        from .declared_effect_amounts import declared_spell_effect_compiler

        body = without_ability_word_label(material_line)
        declared = declared_spell_effect_compiler(record, is_spell=True, oracle_text=oracle_text,
                                                  compile_effect=compile_effect)
        if body is not None and declared(body, card_name=source_name)[0] == VARIABLE_DAMAGE_TEMPLATE_ID:
            return None
    if spell and "spell is countered this way" in material_line.casefold():
        from ..zone_trigger_events import COUNTER_DESTINATION_FIELD
        from .declared_effect_amounts import declared_spell_effect_compiler

        body = without_ability_word_label(material_line)
        if body is not None:
            declared = declared_spell_effect_compiler(
                record, is_spell=True, oracle_text=oracle_text, compile_effect=compile_effect,
            )
            _, effects, _, _ = declared(body, card_name=source_name)
            if (len(effects) == 1 and effects[0].get("op") in {"counter_stack_target", "counter_unless_pay"}
                    and COUNTER_DESTINATION_FIELD in effects[0]):
                return None
    if spell:
        from .reanimation_entry_counter_templates import conditional_reanimation_entry_counter_template
        if conditional_reanimation_entry_counter_template(material_line) is not None:
            return None
    linked_reanimation = fixed_reanimation_followup_template(material_line) if spell else None
    if linked_reanimation is not None and linked_reanimation.followup.exile_on_leave:
        return None
    if spell and "without paying its mana cost" in material_line.casefold():
        from .targeted_graveyard_cast_templates import targeted_graveyard_cast_template
        if targeted_graveyard_cast_template(material_line) is not None:
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
