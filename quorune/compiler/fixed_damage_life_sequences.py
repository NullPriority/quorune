"""Compose fixed damage and independent fixed life gain in source context."""
import re


def fixed_damage_life_clauses(text):
    from .fixed_effect_clause_sequences import _top_level_scope_text
    material=' '.join(text.strip().split())
    pattern=r'(?P<damage>.+? deals [1-9][0-9]* damage to [^.]+?) and you gain (?P<life>[1-9][0-9]*) life\.'
    match=re.fullmatch(pattern,material,re.I)
    if match is None or re.fullmatch(pattern,_top_level_scope_text(material),re.I) is None:return None
    return match['damage']+'.','You gain '+match['life']+' life.'


def fixed_damage_life_sequence_template(text,*,compile_clause):
    if fixed_damage_life_clauses(text) is None:return None
    from .fixed_effect_clause_sequences import fixed_effect_clause_sequence_template
    result=fixed_effect_clause_sequence_template(text,compile_clause=compile_clause)
    return result.compiled() if result is not None else None


def binding_damage_life_result(binding,body,*,card_name,effect_template):
    if fixed_damage_life_clauses(body) is None:return None
    from .trigger_result_templates import binding_effect_template
    current=[]
    def compile_child(text):
        compiled,source_current=binding_effect_template(binding,text,card_name=card_name,effect_template=effect_template)
        current.append(source_current);return compiled
    compiled=fixed_damage_life_sequence_template(body,compile_clause=compile_child)
    return (compiled,any(current)) if compiled is not None else None
