"""Closed intrinsic Demonstrate instruction over the canonical cast-copy snapshot."""
from collections.abc import Mapping

from ..event_spell_copy_model import EVENT_SPELL_COPY_CONTEXT
from ..replacement.immutable import FrozenMap

DEMONSTRATE_CAPABILITY = 'keyword.demonstrate.intrinsic'
DEMONSTRATE_FIELD = 'demonstrate'
DEMONSTRATE_SOURCE = '$context.' + EVENT_SPELL_COPY_CONTEXT
DEMONSTRATE_DEPENDENCIES = (
    DEMONSTRATE_CAPABILITY, 'stack.copy.cast_event_spell',
    'trigger.event.normalized_spell_cast', 'trigger.effect.fixed_event', 'trigger.placement.apnap',
)


def demonstrate_instruction():
    return {'op':'choose_objects','player':'$controller',
        DEMONSTRATE_FIELD:{'schema_version':1},'copy_source':DEMONSTRATE_SOURCE,'stack':'$context.stack'}


def demonstrate_condition():
    return {'all':[{'field':'controller','op':'eq','value':'$source.controller'},
        {'field':'card','op':'eq','value':'$source.ref'}]}


def demonstrate_payload_dependencies(effect):
    if not isinstance(effect,Mapping) or DEMONSTRATE_FIELD not in effect:
        return ()
    marker=effect[DEMONSTRATE_FIELD]
    if (set(effect)!={'op','player',DEMONSTRATE_FIELD,'copy_source','stack'}
            or effect.get('op')!='choose_objects' or effect.get('player')!='$controller'
            or not isinstance(marker,Mapping) or set(marker)!={'schema_version'}
            or type(marker['schema_version']) is not int or marker['schema_version']!=1
            or effect.get('copy_source')!=DEMONSTRATE_SOURCE or effect.get('stack')!='$context.stack'):
        raise ValueError('Demonstrate requires one explicit intrinsic cast-copy instruction')
    return DEMONSTRATE_DEPENDENCIES


def demonstrate_program_dependencies(program):
    if not any(isinstance(effect,Mapping) and DEMONSTRATE_FIELD in effect for effect in program.effects):
        return ()
    if (len(program.effects)!=1 or program.target_schema is not None
            or program.event!='spell.cast' or program.active_zone!='stack'
            or FrozenMap(program.event_condition or {})!=FrozenMap(demonstrate_condition())):
        raise ValueError('Intrinsic Demonstrate requires the actual source spell cast')
    return demonstrate_payload_dependencies(program.effects[0])


def capture_demonstrate_cast_source(host, source, event):
    if (source.zone!='stack' or not isinstance(event,Mapping)
            or event.get('card')!=source.ref or event.get('controller')!=source.controller):
        raise ValueError('Demonstrate source requires its actual source spell cast')
    item=next((item for item in host.state.stack if item.ref==event.get('stack')),None)
    if (item is None or item.kind!='spell' or item.card_object_id!=source.object_id
            or item.controller!=source.controller
            or event.get('logical_object_id')!=source.logical_object_id):
        raise ValueError('Demonstrate cast source changed its stack object or incarnation')
    from .event_spell_copy import capture_event_spell_copy_source
    return capture_event_spell_copy_source(host,item)


def resolve_demonstrate_source(host, state):
    from .event_spell_copy import resolve_event_spell_copy_source
    return resolve_event_spell_copy_source(host,{'stack':state.source.item().ref,
        'copy_source':state.source.to_dict()})


def demonstrate_node_capabilities(*, effects, target_schema, mechanic_ids=(), cost_schema=None):
    if target_schema is not None or len(effects) != 1:
        return ()
    try:
        return demonstrate_payload_dependencies(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()
