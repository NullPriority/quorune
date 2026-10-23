from __future__ import annotations

"""Complete instead clauses that replace a fixed target predicate or count."""

from copy import deepcopy
import re
from ..kicked_target_replacement_model import validate_kicked_target_replacement_instruction
from ..resolution_conditions import RESOLUTION_CONDITION_MECHANIC,CAST_FACT_CONDITION_MECHANIC
from .closed_effect_programs import _top_level_positions


def kicked_target_replacement_template(text,*,compile_component):
    text=text.strip();positions=_top_level_positions(text,'If this spell was kicked, ')
    if positions is None or len(positions)!=1 or not positions[0]:return None
    normal_text=text[:positions[0]].strip();body=text[positions[0]+len('If this spell was kicked, '):].strip()
    leading=re.fullmatch(r'instead (?P<body>.+)',body,re.I)
    trailing=re.fullmatch(r'(?P<body>.+?) instead\.',body,re.I)
    match=leading or trailing
    if match is None:return None
    alternative=match['body'].rstrip('.')+'.'
    if re.search(r'\b(?:instead|if|another|different|this way|equal to|may)\b',alternative,re.I):return None
    normal=compile_component(normal_text);kicked=compile_component(alternative)
    if any(not r[0] or not r[1] or r[2] is None or type(r[2].get('count')) is not int for r in (normal,kicked)):return None
    if normal[2]==kicked[2]:return None
    binding={'schema_version':1,'normal':deepcopy(normal[2]),'kicked':deepcopy(kicked[2])}
    wrapper={'op':'apply_if_public_condition','schema_version':11,'player':'$controller',
        'condition':{'schema_version':1,'kind':'kicked_cast'},'cast_fact':'$context.kicked',
        'effects':deepcopy(list(kicked[1])),'mechanic_ids':list(kicked[3]),
        'otherwise_effects':deepcopy(list(normal[1])),'otherwise_mechanic_ids':list(normal[3]),'target_binding':binding}
    try:validate_kicked_target_replacement_instruction(wrapper)
    except (TypeError,ValueError):return None
    return ('fixed-kicked-target-replacement-v1',(wrapper,),deepcopy(kicked[2]),
        tuple(dict.fromkeys((RESOLUTION_CONDITION_MECHANIC,CAST_FACT_CONDITION_MECHANIC,*normal[3],*kicked[3]))))
