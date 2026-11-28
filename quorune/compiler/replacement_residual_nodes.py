from __future__ import annotations

"""Keep unowned replacement ordering residual after closed static dispatch."""

from . import regex_working_set as re

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


def _owned_damage_result_replacement(material_line, *, spell, compile_effect, source_name):
    normalized = material_line.casefold()
    if spell and "excess damage is dealt" in normalized:
        from ..creature_power_damage_model import TRAMPLE_BITE_CAPABILITY
        from ..rules.creature_power_damage_capability_shapes import fixed_creature_power_damage_node_capabilities

        _, effects, schema, mechanics = compile_effect(material_line, card_name=source_name)
        return TRAMPLE_BITE_CAPABILITY in fixed_creature_power_damage_node_capabilities(
            effects=effects, target_schema=schema, mechanic_ids=mechanics,
        )
    if "would die this turn, exile it instead." not in normalized:
        return False
    body = material_line if spell else material_line.partition(": ")[2]
    return bool(body and DAMAGE_DEATH_EXILE_MECHANIC in compile_effect(body, card_name=source_name)[3])


def unowned_replacement_node(*,node_id,line,material_line,span,source_name,
        spell,permanent,record,oracle_text,compile_effect,residuals):
    if not _REPLACEMENT_MARKERS.search(material_line):
        if not _REPLACEMENT_MARKERS.search(line) or _balanced_reminder_delimiters(line):
            return None
    if spell and printed_bargain_spec(record) is not None and compile_effect(material_line,card_name=source_name,bargain_available=True)[0] in {'linked-bargain-same-target-v1','linked-bargain-procedure-replacement-v1'}:
        return None
    if spell and is_fixed_spell_self_replacement(material_line,source_name,
            compile_effect,has_faces=bool(record.faces)):
        return None
    if spell and material_line.casefold().startswith("draw x cards."):
        from ..rules.announced_x_draw_replacement import TEMPLATE
        from .declared_effect_amounts import declared_spell_effect_compiler

        declared = declared_spell_effect_compiler(
            record, is_spell=True, oracle_text=oracle_text, compile_effect=compile_effect,
        )
        template = declared(material_line, card_name=source_name)[0]
        if template is not None and TEMPLATE in template.split("+"):
            return None
    if spell and "damage that would reduce your life total" in material_line.casefold():
        from ..rules.turn_match_outcome_protection import TURN_OUTCOME_TEMPLATE
        from .damage_permission_templates import turn_damage_prevention_template

        result = turn_damage_prevention_template(material_line, card_name=source_name, source_is_permanent=False)
        if result is not None and result[0] == TURN_OUTCOME_TEMPLATE:
            return None
    if spell and not record.faces:
        from .graveyard_cast_replacement import cast_origin_replacement_template
        if cast_origin_replacement_template(
            material_line, source_name=source_name, compile_component=lambda body: compile_effect(body, card_name=source_name),
        ) is not None:
            return None
    if spell and re.search(r"\bX plus 2 damage\b", material_line, re.I):
        from .declared_effect_amounts import declared_spell_effect_compiler

        declared = declared_spell_effect_compiler(record, is_spell=True, oracle_text=oracle_text, compile_effect=compile_effect)
        if declared(material_line, card_name=source_name)[0] == "public-same-target-self-replacement-v1":
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
        from ..rules.stack_counter_followup import (
            STACK_LIBRARY_COUNTER_POSITION_CAPABILITY, stack_counter_followup_node_capabilities,
        )
        from ..zone_trigger_events import COUNTER_DESTINATION_FIELD
        from .declared_effect_amounts import declared_spell_effect_compiler

        body = without_ability_word_label(material_line)
        if body is not None:
            declared = declared_spell_effect_compiler(
                record, is_spell=True, oracle_text=oracle_text, compile_effect=compile_effect,
            )
            _, effects, target, mechanics = declared(body, card_name=source_name)
            if (len(effects) == 1 and effects[0].get("op") in {"counter_stack_target", "counter_unless_pay"}
                    and COUNTER_DESTINATION_FIELD in effects[0]):
                return None
            if STACK_LIBRARY_COUNTER_POSITION_CAPABILITY in stack_counter_followup_node_capabilities(
                effects=effects, target_schema=target, mechanic_ids=mechanics,
            ):
                return None
    if spell:
        if 'instead if ' in material_line.casefold() and re.search(r'\bThen (?:it|that creature) (?:fights|deals damage equal to its power to) ', material_line, re.I):
            from ..rules.conditional_target_preparation import conditional_prepared_power_capabilities
            compiled = compile_effect(material_line, card_name=source_name)
            if compiled[0] == 'conditional-target-power-preparation-v1' and conditional_prepared_power_capabilities(
                    effects=compiled[1], target_schema=compiled[2], mechanic_ids=compiled[3]):
                return None
        from .linked_exile_return_templates import prospective_entry_linked_return_template
        if prospective_entry_linked_return_template(material_line) is not None:
            return None
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
    if spell and material_line.casefold().startswith("until end of turn, if target player would draw "):
        from ..draw_redirect_model import temporary_draw_redirect_node_capabilities

        _, effects, target, mechanics = compile_effect(material_line, card_name=source_name)
        if temporary_draw_redirect_node_capabilities(
            effects=effects, target_schema=target, mechanic_ids=mechanics,
        ):
            return None
    if spell and re.search(r"\bskip\b", material_line, re.I):
        if TURN_SKIP_MECHANIC in compile_effect(material_line, card_name=source_name)[3]:
            return None
    body = material_line if spell else material_line.partition(': ')[2]
    if body.casefold().startswith("the next time you would draw a card this turn"):
        from .next_draw_replacement_templates import next_draw_replacement_template

        if next_draw_replacement_template(body) is not None:
            return None
    if _owned_damage_result_replacement(material_line, spell=spell, compile_effect=compile_effect, source_name=source_name):
        return None
    residual_id=append_residual(residuals,kind='replacement_effect',text=line,span=span,
        reason='replacement/prevention ordering is not compiled',blockers=(
            'replacement applicability','affected-player ordering',
            'self-replacement and prevention ordering'))
    return OracleNode(node_id=node_id,kind='replacement_effect',text=line,span=span,
        active_zone='battlefield' if permanent else 'stack',event='replace',
        lowerable=False,exact=False,residual_ids=(residual_id,))
