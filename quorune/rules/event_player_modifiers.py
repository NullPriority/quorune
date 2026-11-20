"""Independent selection/frame proof for the versioned event-player stat set."""
from collections.abc import Mapping
from dataclasses import replace
from .qualified_attacks import qualified_attack_defender_condition_is_closed
from ..event_player_modifier_model import EventPlayerModifierSpec,EVENT_PLAYER_SET_VERSION_FIELD,EVENT_PLAYER_SET_CAPABILITY,EVENT_PLAYER_SET_MECHANIC
from ..object_predicate import ObjectQuerySpec


def event_player_modifier_scope_dependencies(event,condition):
    if event!='creature.attacks':return ()
    from .event_player_result_shapes import _source_combat_player_scope
    if _source_combat_player_scope(condition):return ('trigger.event.normalized_self_attack',)
    if qualified_attack_defender_condition_is_closed(event,condition):
        return ('trigger.event.normalized_public_action','trigger.event.qualified_attack_singular')
    return ()


def event_player_modifier_node_capabilities(*,effects,target_schema,mechanic_ids):
    if (target_schema is not None or len(effects)!=1 or EVENT_PLAYER_SET_MECHANIC not in set(mechanic_ids)
            or 'cr-611-continuous-effects' not in set(mechanic_ids)):return ()
    try:
        spec=EventPlayerModifierSpec.from_effect(effects[0]);projected=spec.to_effect();projected.pop(EVENT_PLAYER_SET_VERSION_FIELD)
        query=ObjectQuerySpec.from_dict(projected['predicate']);projected['predicate']=replace(query,controller='$controller').to_dict()
        from .fixed_resolution_characteristic_shapes import fixed_resolution_characteristic_set_node_capabilities
        base=fixed_resolution_characteristic_set_node_capabilities(effects=(projected,),target_schema=None,mechanic_ids=('cr-611-continuous-effects',))
    except (TypeError,ValueError,KeyError):return ()
    return tuple(sorted({EVENT_PLAYER_SET_CAPABILITY,*base})) if base else ()


def event_player_modifier_program_dependencies(program):
    def contains(value):
        if isinstance(value,Mapping):return EVENT_PLAYER_SET_VERSION_FIELD in value or any(contains(v) for v in value.values())
        return isinstance(value,(list,tuple)) and any(contains(v) for v in value)
    if not contains(program.effects):return ()
    if (program.event!='creature.attacks' or program.active_zone!='battlefield'
            or program.target_schema is not None or getattr(program,'cost_schema',None) is not None or len(program.effects)!=1):
        raise ValueError('Event-player stat set requires its complete untargeted attack frame')
    scope=event_player_modifier_scope_dependencies(program.event,program.event_condition)
    owners=event_player_modifier_node_capabilities(effects=program.effects,target_schema=None,
        mechanic_ids=(EVENT_PLAYER_SET_MECHANIC,'cr-611-continuous-effects'))
    if not scope or not owners:raise ValueError('Event-player stat set has an unproved recipient, query or result')
    return tuple(sorted({*scope,*owners,'trigger.placement.apnap'}))


def validate_bound_event_player_modifier(effect):
    """Validate a resolved producer before the server adds runtime context."""
    return EventPlayerModifierSpec.from_effect(effect,resolved=True)


def prepare_event_player_modifier_runtime(host,effect):
    """Project a validated bound instruction to the existing canonical selector."""
    runtime={k:v for k,v in effect.items() if k=='_runtime_source'}
    public={k:v for k,v in effect.items() if k not in runtime}
    spec=validate_bound_event_player_modifier(public)
    player=ObjectQuerySpec.from_dict(public['predicate']).controller
    if player not in host.state.players:raise ValueError('Event-player modifier has an unknown bound player')
    if player not in host.active_seats:return None
    return {**spec.bound_effect(player),**runtime}


def is_closed_event_player_modifier_program(program):
    try:required=event_player_modifier_program_dependencies(program)
    except (TypeError,ValueError,KeyError):return False
    return bool(required) and set(required)<=set(program.capability_dependencies)


def event_player_modifier_covered_mechanics(supplied):
    return {EVENT_PLAYER_SET_MECHANIC} if EVENT_PLAYER_SET_CAPABILITY in supplied else set()
