from __future__ import annotations

"""Bind conditional target cardinality to the already selected Kicker price."""

from copy import deepcopy
from ...kicked_target_model import bound_kicked_target_schema


def bind_kicked_targets(program,option):
    wrappers=[e for e in getattr(program,'effects',()) if e.get('op')=='apply_if_public_condition' and e.get('schema_version')==10]
    if not wrappers:return option
    if len(wrappers)!=1 or wrappers[0]!=program.effects[-1]:raise ValueError('Kicked targets require one final source-bound instruction')
    from ..kicked_target_shapes import kicked_target_capabilities
    if not kicked_target_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage):
        raise ValueError('Kicked target roles lack complete source closure')
    expected=bound_kicked_target_schema(wrappers[0]['target_binding'],option.get('kind')=='kicker')
    if option.get('target_schema') is not None and option['target_schema']!=expected:raise ValueError('Kicked target option changed its source-bound roles')
    return {**deepcopy(option),'target_schema':expected}


def validate_kicked_target_commit(program,option):
    if not any(e.get('op')=='apply_if_public_condition' and e.get('schema_version')==10 for e in getattr(program,'effects',())):return
    expected=bind_kicked_targets(program,{k:v for k,v in option.items() if k!='target_schema'})
    if expected.get('target_schema')!=option.get('target_schema'):
        raise ValueError('Kicker choice and current target schema no longer agree')
