from __future__ import annotations

"""Fixed result self-replacement using an already sealed Kicker choice."""

from copy import deepcopy
import re

from ..resolution_conditions import (
    RESOLUTION_CONDITION_OPERATION, RESOLUTION_CONDITION_MECHANIC,
    CAST_FACT_CONDITION_MECHANIC, KickedCastCondition, cast_fact_result_is_fixed,
)
from .closed_effect_programs import _top_level_positions
from .fixed_numbers import FIXED_COUNT_PATTERN
from .public_self_replacement_templates import _alternative_body,is_fixed_public_self_replacement
from .token_templates import _positive_fixed_number


def is_fixed_kicked_self_replacement(text, source_name, compile_effect):
    return compile_effect(text,card_name=source_name,
        kicked_self_replacement_available=True)[0] in {'fixed-kicked-self-replacement-v1','fixed-kicked-target-replacement-v1'}


def is_fixed_spell_self_replacement(text,source_name,compile_effect,*,has_faces):
    return (is_fixed_public_self_replacement(text,source_name,compile_effect)
        or not has_faces and is_fixed_kicked_self_replacement(text,source_name,compile_effect))


def _replacement_component(alternative, normal, compile_component,source_name):
    compiled=compile_component(alternative)
    if compiled[0] is not None:
        return compiled
    if (len(normal[1])==1 and normal[1][0].get('op')=='damage_fixed_set'
            and normal[1][0].get('source')=='$source' and re.match(r'^it deals ',alternative,re.I)):
        return compile_component(source_name+alternative[len('it'):])
    # "Those tokens" retains the complete original recipe, including its types,
    # colors and abilities; only the literal count changes.
    count=re.fullmatch(rf'Create (?P<count>{FIXED_COUNT_PATTERN}|eleven|twelve|thirteen) of those tokens\.',alternative,re.I)
    if (count is None or normal[2] is not None or len(normal[1])!=1
            or normal[1][0].get('op')!='create_token'):
        return None
    quantity=_positive_fixed_number(count['count'])
    if quantity is None or quantity<1:
        return None
    effect=deepcopy(normal[1][0]);effect['quantity']=quantity
    return normal[0],(effect,),None,normal[3]


def kicked_self_replacement_template(text, *, source_name,compile_component):
    positions=_top_level_positions(text.strip(),'If ')
    if positions is None or len(positions)!=1:
        return None
    text=text.strip();position=positions[0]
    normal_text=text[:position].strip()
    clause=re.fullmatch(r'If (?:this spell|it) was kicked, (?P<body>.+)',text[position:],re.I)
    if clause is None or not normal_text.endswith('.'):
        return None
    alternative=_alternative_body(normal_text,clause['body'])
    if alternative is None:
        return None
    normal=compile_component(normal_text)
    if normal[0] is None or len(normal[1])!=1:
        return None
    targeted=normal[2] is not None
    if targeted:
        from .kicked_same_target_replacements import same_target_kicked_replacement
        replacement=same_target_kicked_replacement(normal_text,alternative,normal,compile_component)
    else:
        replacement=_replacement_component(alternative,normal,compile_component,source_name)
    if replacement is None or not 1<=len(replacement[1])<=(3 if targeted else 1) or replacement[2]!=normal[2]:
        return None
    if not all(cast_fact_result_is_fixed(value[1],value[3],allow_fixed_damage_source=True,
            allow_targeted_damage_source=targeted) for value in (normal,replacement)):
        return None
    wrapper={'op':RESOLUTION_CONDITION_OPERATION,'schema_version':5 if targeted else 4,'player':'$controller',
        'condition':KickedCastCondition().to_dict(),'cast_fact':'$context.kicked',
        'effects':deepcopy(list(replacement[1])),'mechanic_ids':list(replacement[3]),
        'otherwise_effects':deepcopy(list(normal[1])),'otherwise_mechanic_ids':list(normal[3]),
        'prefix_mechanic_ids':[]}
    return ('fixed-kicked-self-replacement-v1',(wrapper,),deepcopy(normal[2]),
        tuple(dict.fromkeys((RESOLUTION_CONDITION_MECHANIC,CAST_FACT_CONDITION_MECHANIC,*normal[3],*replacement[3]))))
