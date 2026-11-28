"""Closed Ripple instruction and cast-source facts; existing owners mutate state."""
from collections.abc import Mapping
from ..replacement.immutable import FrozenMap
from ..ripple_model import RippleSpec,RippleCastSnapshot,RIPPLE_CAPABILITY
from .revealed_library_cast import (
    MANA_VALUE_CAST_FIELD, decode_mana_value_cast_instruction, mana_value_cast_payload_dependencies,
    mana_value_cast_program_dependencies,
)

RIPPLE_FIELD = 'ripple'
RIPPLE_CAST_CONTEXT = 'ripple_cast_snapshot'
RIPPLE_SNAPSHOT_VALUE = '$context.' + RIPPLE_CAST_CONTEXT
RIPPLE_MECHANIC = 'ripple'
RIPPLE_TRIGGER_CAPABILITIES = (
    RIPPLE_CAPABILITY,'trigger.effect.fixed_event',
    'trigger.event.normalized_spell_cast','trigger.placement.apnap',
)


def ripple_event_condition():
    return {'all':[{'field':'controller','op':'eq','value':'$source.controller'},
        {'field':'card','op':'eq','value':'$source.ref'}]}


def ripple_instruction(spec):
    if not isinstance(spec,RippleSpec):raise ValueError('Ripple requires its typed count')
    return {'op':'choose_objects','player':'$controller',RIPPLE_FIELD:spec.to_dict(),
        'cast_snapshot':RIPPLE_SNAPSHOT_VALUE}


def decode_ripple_instruction(effect,*,actor='$controller',allow_symbolic=True):
    if (not isinstance(effect,Mapping) or set(effect)!={'op','player',RIPPLE_FIELD,'cast_snapshot'}
            or effect['op']!='choose_objects' or effect['player']!=actor):
        raise ValueError('Ripple requires one complete resolving-controller instruction')
    spec=RippleSpec.from_dict(effect[RIPPLE_FIELD]);raw=effect['cast_snapshot']
    if allow_symbolic:
        if raw!=RIPPLE_SNAPSHOT_VALUE:raise ValueError('Compiled Ripple requires captured actual-cast facts')
        return spec,None
    return spec,RippleCastSnapshot.from_dict(raw)


def uses_revealed_library_cast(effect):
    return isinstance(effect, Mapping) and (RIPPLE_FIELD in effect or MANA_VALUE_CAST_FIELD in effect)


def decode_revealed_library_cast(effect, *, actor, allow_symbolic):
    if MANA_VALUE_CAST_FIELD in effect:
        return None, decode_mana_value_cast_instruction(effect, actor=actor, allow_symbolic=allow_symbolic)
    return decode_ripple_instruction(effect, actor=actor, allow_symbolic=allow_symbolic)


def ripple_payload_dependencies(effect):
    if not uses_revealed_library_cast(effect):return ()
    if MANA_VALUE_CAST_FIELD in effect:
        return mana_value_cast_payload_dependencies(effect)
    decode_ripple_instruction(effect)
    return RIPPLE_TRIGGER_CAPABILITIES


def ripple_program_dependencies(program):
    if any(isinstance(e, Mapping) and MANA_VALUE_CAST_FIELD in e for e in program.effects):
        return mana_value_cast_program_dependencies(program)
    if not any(isinstance(e,Mapping) and RIPPLE_FIELD in e for e in program.effects):return ()
    if (len(program.effects)!=1 or program.target_schema is not None
            or program.event!='spell.cast' or program.active_zone!='stack'
            or FrozenMap(program.event_condition or {})!=FrozenMap(ripple_event_condition())):
        raise ValueError('Ripple requires its actual source spell cast and independent trigger')
    return ripple_payload_dependencies(program.effects[0])


def capture_ripple_cast_snapshot(host,source,event,*,characteristics=None):
    if (source.zone!='stack' or not isinstance(event,Mapping)
            or event.get('card')!=source.ref or event.get('controller')!=source.controller):
        raise ValueError('Ripple cast facts require the actual source card on stack')
    from ..carddb_characteristics import public_stack_rules_names
    data=host._effective_card_data(source) if characteristics is None else characteristics
    names=tuple(sorted(set(public_stack_rules_names(source,data,host.card_record(source)))))
    return RippleCastSnapshot(source.controller,event.get('stack'),names)


def ripple_node_capabilities(*,effects,target_schema,mechanic_ids=(),cost_schema=None):
    if target_schema is not None or len(effects)!=1:return ()
    try:return ripple_payload_dependencies(effects[0])
    except (TypeError,ValueError,KeyError):return ()
