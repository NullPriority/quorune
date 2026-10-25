from __future__ import annotations

"""One fixed default instruction replaced before its ordinary event owners."""

from copy import deepcopy
import re
from .closed_effect_programs import _top_level_positions
from .public_state_queries import fixed_public_state_condition
from ..resolution_conditions import RESOLUTION_CONDITION_OPERATION,RESOLUTION_CONDITION_MECHANIC,cast_fact_result_is_fixed,resolution_condition_is_closed,public_replacement_result_is_fixed,public_source_replacement_result_is_fixed


def _public_replacement_condition(text,source_name):
    if text.casefold() in {'you had a land enter the battlefield under your control this turn',
            'a land entered the battlefield under your control this turn'}:
        from ..continuous_conditions import FixedPublicStateConditionSpec,FixedPublicStateConditionKind,FixedPublicStateFact
        return FixedPublicStateConditionSpec(FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST,
            amount=1,fact=FixedPublicStateFact.CONTROLLER_LANDS_ENTERED_THIS_TURN,schema_version=4)
    return fixed_public_state_condition(text,source_name=source_name)


def normalize_public_replacement_text(text):
    """Ability words label a complete clause; they do not select its rules."""
    text=text.strip()
    separators=_top_level_positions(text,'. ')
    if separators is None:return text
    for position in reversed(separators):
        start=position+2
        word=re.match(r'[A-Z][A-Za-z0-9\x27 -]{0,80} — ',text[start:])
        if word is not None:text=text[:start]+text[start+word.end():]
    positions=_top_level_positions(text,' instead if ')
    if positions is not None and len(positions)==1:
        position=positions[0];before=text[:position];condition=text[position+len(' instead if '):]
        stops=_top_level_positions(before,'. ')
        if stops and condition.endswith('.'):
            split=stops[-1];prefix=before[:split+1];alternative=before[split+2:]
            return prefix+' If '+condition[:-1]+', '+alternative+' instead.'
    return text


def is_fixed_public_self_replacement(text,source_name,compile_effect):
    return fixed_public_self_replacement_template(text,source_name=source_name,
        compile_component=lambda body:compile_effect(body,card_name=source_name)) is not None


def _alternative_body(base,alternative):
    if alternative.casefold().startswith('instead '):
        alternative=alternative[len('instead '):]
        if alternative.casefold().startswith('only '):alternative=alternative[len('only '):]
    elif alternative.casefold().endswith(' instead.'):
        alternative=alternative[:-len(' instead.')]+'.'
    else:return None
    if re.search(r'\b(?:may|instead|if|this way|that much)\b',base+' '+alternative,re.I):return None
    if alternative.casefold().startswith('those creatures '):
        subject=re.match(r'(?P<subject>Creatures you control) get ',base,re.I)
        if subject is None:return None
        alternative=subject['subject']+alternative[len('those creatures'):]
    return alternative


def fixed_public_self_replacement_template(text,*,source_name,compile_component):
    text=normalize_public_replacement_text(text);positions=_top_level_positions(text,'If ')
    if positions is None or len(positions)!=1 or not text.endswith('.'):return None
    position=positions[0];before=text[:position].strip();after=text[position+3:]
    if not before.endswith('.'):return None
    stops=_top_level_positions(before[:-1],'. ')
    if stops is None:return None
    prefix=before[:stops[-1]+1] if stops else ''
    base=before[stops[-1]+2:] if stops else before
    commas=_top_level_positions(after,', ')
    if commas is None:return None
    results=[]
    for comma in commas:
        condition=_public_replacement_condition(after[:comma],source_name)
        alternative=_alternative_body(base,after[comma+2:])
        if condition is None or alternative is None or not resolution_condition_is_closed(condition):continue
        normal=compile_component(base)
        targeted=normal[2] is not None
        if targeted:
            from .public_replacement_bindings import public_same_target_replacement
            replacement=public_same_target_replacement(base,alternative,normal,compile_component,source_name)
        else:
            from .kicked_self_replacement_templates import _replacement_component
            replacement=_replacement_component(alternative,normal,compile_component,source_name)
        if replacement is None:continue
        mandatory=compile_component(prefix) if prefix else (None,(),None,())
        if (normal[0] is None or replacement[0] is None or len(normal[1])!=1 or not 1<=len(replacement[1])<=(3 if targeted else 1)
                or replacement[2]!=normal[2] or targeted and prefix or prefix and mandatory[0] is None):continue
        source_result=not targeted and not all(cast_fact_result_is_fixed(value[1],value[3]) for value in (normal,replacement))
        fixed=public_replacement_result_is_fixed if targeted else public_source_replacement_result_is_fixed if source_result else cast_fact_result_is_fixed
        if not all(fixed(value[1],value[3]) for value in (normal,replacement)):continue
        if len(mandatory[1])+1>8:continue
        effect={'op':RESOLUTION_CONDITION_OPERATION,'schema_version':8 if targeted else 9 if source_result else 3,'player':'$controller','condition':condition.to_dict(),
            'effects':deepcopy(list(replacement[1])),'mechanic_ids':list(replacement[3]),
            'otherwise_effects':deepcopy(list(normal[1])),'otherwise_mechanic_ids':list(normal[3]),
            'prefix_mechanic_ids':list(mandatory[3])}
        if targeted:effect['target_binding']=deepcopy(normal[2])
        if source_result:effect['source_binding']='$source'
        results.append(('public-same-target-self-replacement-v1' if targeted else 'public-source-self-replacement-v1' if source_result else 'fixed-public-self-replacement-v1',
            (*deepcopy(mandatory[1]),effect),deepcopy(normal[2]) if targeted else mandatory[2],
            tuple(dict.fromkeys((RESOLUTION_CONDITION_MECHANIC,*mandatory[3],*normal[3],*replacement[3])))))
    return results[0] if len(results)==1 else None
