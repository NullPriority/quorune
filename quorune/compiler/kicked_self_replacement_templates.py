from __future__ import annotations

"""Fixed result self-replacement using an already sealed Kicker choice."""

from copy import deepcopy
from . import regex_working_set as re

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


def _kicked_untap_stat_replacement(text, compile_component):
    match = re.fullmatch(
        r"Untap target creature\. It gets (?P<ordinary>[+-]\d+/[+-]\d+) until end of turn\. "
        r"If this spell was kicked, that creature gets (?P<kicked>[+-]\d+/[+-]\d+) until end of turn instead[.]?",
        text.strip(), re.I,
    )
    if match is None:
        return None
    untap = compile_component("Untap target creature.")
    normal_stats = compile_component(f'Target creature gets {match["ordinary"]} until end of turn.')
    kicked_stats = compile_component(f'Target creature gets {match["kicked"]} until end of turn.')
    if (
        untap[0] is None or untap[2] is None or tuple(untap[1]) != ({"op": "untap", "card": "$target.0"},)
        or any(result[0] is None or result[2] != untap[2] or len(result[1]) != 1
               or result[1][0].get("op") != "modify_stats_until_end_of_turn"
               or result[1][0].get("card") != "$target.0" for result in (normal_stats, kicked_stats))
    ):
        return None
    from .bound_effect_programs import _program

    normal = _program((untap, normal_stats), schema=untap[2])
    kicked = _program((untap, kicked_stats), schema=untap[2])
    if normal is None or kicked is None:
        return None
    return _kicked_condition_branches(normal.compiled(), kicked.compiled(), targeted=True)


def kicked_self_replacement_template(text, *, source_name,compile_component):
    shared = _kicked_untap_stat_replacement(text, compile_component)
    if shared is not None:
        return shared
    from .procedure_variants import literal_procedure_variant
    procedure = literal_procedure_variant(text)
    if procedure is not None and procedure.condition.casefold() == 'this spell was kicked':text = procedure.expanded()
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
    return _kicked_condition_branches(normal, replacement, targeted=targeted)


def kicked_additive_target_lifelink_template(text, *, compile_component):
    """Keep the mandatory stat result in both arms of the immutable cast fact."""
    match = re.fullmatch(
        r"(?P<base>Target creature gets [+-]\d+/[+-]\d+ until end of turn\.) "
        r"If this spell was kicked, that creature gains lifelink until end of turn[.]?",
        text.strip(), re.I,
    )
    if match is None:
        return None
    normal = compile_component(match["base"])
    rider = compile_component("Target creature gains lifelink until end of turn.")
    if (
        normal[0] is None or rider[0] is None or normal[2] is None or normal[2] != rider[2]
        or len(normal[1]) != 1 or normal[1][0].get("op") != "modify_stats_until_end_of_turn"
        or normal[1][0].get("card") != "$target.0"
        or tuple(rider[1]) != ({"op": "grant_keyword_until_end_of_turn", "card": "$target.0", "keyword": "Lifelink"},)
    ):
        return None
    kicked = (normal[0], (*normal[1], *rider[1]), normal[2], tuple(dict.fromkeys((*normal[3], *rider[3]))))
    return _kicked_condition_branches(normal, kicked, targeted=True)


def _kicked_condition_branches(normal, replacement, *, targeted):
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
