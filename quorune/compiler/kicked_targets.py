from __future__ import annotations

"""A complete fixed Kicker-dependent target with an independent prefix."""

from copy import deepcopy
import re
from ..kicked_target_model import scope_target_role,bound_kicked_target_schema,validate_kicked_target_instruction
from ..resolution_conditions import RESOLUTION_CONDITION_MECHANIC,CAST_FACT_CONDITION_MECHANIC
from .closed_effect_programs import _top_level_positions


def kicked_target_template(text,*,source_name,compile_component):
    text=text.strip();positions=_top_level_positions(text,'If this spell was kicked, ')
    if positions is None or len(positions)!=1:return None
    pos=positions[0];prefix_text=text[:pos].strip();body=text[pos+len('If this spell was kicked, '):]
    if re.search(r'\b(?:instead|this way|that much|equal to|otherwise|if|may)\b',body,re.I):return None
    distinct=bool(re.search(r'\banother target\b',body,re.I))
    if re.search(r'\banother target\.',body,re.I) and re.search(r'\bany target\.',prefix_text,re.I):
        body=re.sub(r'\banother target\.', 'any target.',body,flags=re.I)
    if re.search(r'\b(?:it also|it) deals ',body,re.I):
        body=re.sub(r'^it (?:also )?deals ',source_name+' deals ',body,flags=re.I)
    body=re.sub(r'\banother target\b','target',body,flags=re.I)
    conditional=compile_component(body)
    if conditional[0] is None or not conditional[1] or conditional[2] is None or conditional[2].get('count')!=1:return None
    prefix=compile_component(prefix_text) if prefix_text else (None,(),None,())
    if prefix_text and prefix[0] is None:return None
    if prefix[2] is not None and prefix[2].get('count')!=1:return None
    if distinct and prefix[2] is None:return None
    groups=[]
    if prefix[2] is not None:groups.append({'id':'target_0','schema':deepcopy(prefix[2]),'different_from_groups':[]})
    groups.append({'id':'target_1','schema':deepcopy(conditional[2]),'different_from_groups':['target_0'] if distinct else []})
    binding={'schema_version':1,'groups':groups,'conditional_group':'target_1'}
    wrapper={'op':'apply_if_public_condition','schema_version':10,'player':'$controller',
        'condition':{'schema_version':1,'kind':'kicked_cast'},'cast_fact':'$context.kicked',
        'effects':scope_target_role(conditional[1],'target_1'),'mechanic_ids':list(conditional[3]),
        'prefix_mechanic_ids':list(prefix[3]),'target_binding':binding}
    try:validate_kicked_target_instruction(wrapper)
    except (TypeError,ValueError):return None
    effects=tuple(scope_target_role(prefix[1],'target_0') if prefix[2] is not None else deepcopy(prefix[1]))+(wrapper,)
    return ('fixed-kicked-additional-target-v1',effects,bound_kicked_target_schema(binding,True),
        tuple(dict.fromkeys((RESOLUTION_CONDITION_MECHANIC,CAST_FACT_CONDITION_MECHANIC,*prefix[3],*conditional[3]))))
