from __future__ import annotations

"""One fixed default instruction replaced before its ordinary event owners."""

from copy import deepcopy
import re
from .closed_effect_programs import _top_level_positions
from .public_state_queries import fixed_public_state_condition
from ..resolution_conditions import RESOLUTION_CONDITION_OPERATION,RESOLUTION_CONDITION_MECHANIC,cast_fact_result_is_fixed,resolution_condition_is_closed


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
    text=text.strip();positions=_top_level_positions(text,'If ')
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
        condition=fixed_public_state_condition(after[:comma],source_name=source_name)
        alternative=_alternative_body(base,after[comma+2:])
        if condition is None or alternative is None or not resolution_condition_is_closed(condition):continue
        normal=compile_component(base);replacement=compile_component(alternative)
        mandatory=compile_component(prefix) if prefix else (None,(),None,())
        if (normal[0] is None or replacement[0] is None or len(normal[1])!=1 or len(replacement[1])!=1
                or normal[2] is not None or replacement[2] is not None or prefix and mandatory[0] is None):continue
        if not all(cast_fact_result_is_fixed(value[1],value[3]) for value in (normal,replacement)):continue
        if len(mandatory[1])+1>8:continue
        effect={'op':RESOLUTION_CONDITION_OPERATION,'schema_version':3,'player':'$controller','condition':condition.to_dict(),
            'effects':deepcopy(list(replacement[1])),'mechanic_ids':list(replacement[3]),
            'otherwise_effects':deepcopy(list(normal[1])),'otherwise_mechanic_ids':list(normal[3]),
            'prefix_mechanic_ids':list(mandatory[3])}
        results.append(('fixed-public-self-replacement-v1',(*deepcopy(mandatory[1]),effect),mandatory[2],
            tuple(dict.fromkeys((RESOLUTION_CONDITION_MECHANIC,*mandatory[3],*normal[3],*replacement[3])))))
    return results[0] if len(results)==1 else None
