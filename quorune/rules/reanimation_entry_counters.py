"""Closed targeted return and entry-counter authority without marker inference."""
from collections.abc import Mapping
from ..reanimation_entry_counters import (REANIMATION_ENTRY_COUNTER_CAPABILITY,
    REANIMATION_ENTRY_COUNTER_MECHANIC,validate_reanimation_entry_effect)
from ..keyword_counters import keyword_counter_mechanic
from .reanimation_capability_shapes import fixed_target_reanimation_node_capabilities


def reanimation_entry_counter_node_capabilities(*,effects,target_schema,mechanic_ids):
    if len(effects)!=1:return ()
    effect=effects[0]
    try:validate_reanimation_entry_effect(effect)
    except (TypeError,ValueError,KeyError):return ()
    base={key:value for key,value in effect.items() if key not in {'schema_version','entry_counter','source'}}
    owned=fixed_target_reanimation_node_capabilities(effects=(base,),target_schema=target_schema,mechanic_ids=mechanic_ids)
    if not owned:return ()
    required={*owned,REANIMATION_ENTRY_COUNTER_CAPABILITY,'counter.producer.effect_entry'}
    keyword=keyword_counter_mechanic(effect['entry_counter']['counter_name'])
    if keyword:
        from .capabilities import MECHANIC_CAPABILITY_DEPENDENCIES
        gameplay=MECHANIC_CAPABILITY_DEPENDENCIES.get(keyword,())
        if not gameplay:return ()
        required.update(('counter.characteristic.keyword',*gameplay))
    return tuple(sorted(required))


def is_closed_reanimation_entry_counter_program(program):
    required=reanimation_entry_counter_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)


def reanimation_entry_counter_runtime_dependencies(program):
    required=set()
    def visit(value):
        if isinstance(value,Mapping):
            if value.get('op')=='reanimate' and (value.get('schema_version')==3
                    or 'entry_counter' in value and value.get('schema_version')!=2):
                owned=reanimation_entry_counter_node_capabilities(effects=(value,),target_schema=program.target_schema,
                    mechanic_ids=('fixed-target-reanimation','cr-115-targets',REANIMATION_ENTRY_COUNTER_MECHANIC))
                if not owned:raise ValueError('Entry-counter reanimation lacks its closed target and counter shape')
                required.update(owned)
            for child in value.values():visit(child)
        elif isinstance(value,(list,tuple)):
            for child in value:visit(child)
    visit(program.effects)
    return tuple(sorted(required))
