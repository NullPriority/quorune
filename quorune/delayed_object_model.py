"""CR603.7: one closed action on one retained battlefield incarnation."""
from collections.abc import Mapping
from .counter_names import normalized_counter_name
from .damage_recipient_model import DAMAGE_PERMANENT_OBJECT,DAMAGE_PERMANENT_CAPABILITY

DELAYED_OBJECT_CAPABILITY='trigger.delayed.fixed_object_action'
DELAYED_OBJECT_MECHANIC='fixed-delayed-object-action'
SCHEDULE_OBJECT_ACTION='schedule_fixed_object_action'
RESOLVE_OBJECT_ACTION='resolve_fixed_object_action'
OBJECT_ACTIONS=frozenset({'destroy','sacrifice','bounce'})
COUNTER_OBJECT_ACTIONS=frozenset({'place_counters','remove_counters'})
OBJECT_TIMINGS={'end_of_combat':{'phase':'combat','step':'end_combat'},
    'next_end_step':{'phase':'ending','step':'end_step'}}
OBJECT_REFERENCES=frozenset({'$source.zone_object','$event.block_counterpart',DAMAGE_PERMANENT_OBJECT})


def validate_delayed_object_instruction(effect,*,compiled=False):
    if not isinstance(effect,Mapping):raise ValueError('Delayed object instruction must be a mapping')
    op=effect.get('op');version=effect.get('schema_version');fields={'op','schema_version','object','action'}
    if type(version)is int and version==2:fields.update({'counter','amount','source'})
    if op==SCHEDULE_OBJECT_ACTION:fields.add('timing')
    if (op not in {SCHEDULE_OBJECT_ACTION,RESOLVE_OBJECT_ACTION} or set(effect)!=fields
            or type(version)is not int or version not in {1,2}
            or type(effect['action'])is not str
            or effect['action']not in (COUNTER_OBJECT_ACTIONS if version==2 else OBJECT_ACTIONS)):
        raise ValueError('Delayed object action has a closed version and operation')
    if version==2 and (type(effect['counter'])is not str or not effect['counter']
            or normalized_counter_name(effect['counter'])!=effect['counter']
            or type(effect['amount'])is not int or effect['amount']<=0
            or type(effect['source'])is not str or not effect['source']
            or compiled and (effect['object']!='$source.zone_object' or effect['source']!='$source')
            or not compiled and effect['source'].startswith('$')):
        raise ValueError('Delayed counters require one canonical fixed amount, name and creating source')
    if op==SCHEDULE_OBJECT_ACTION and (type(effect['timing'])is not str or effect['timing']not in OBJECT_TIMINGS):
        raise ValueError('Delayed object timing is outside the closed family')
    obj=effect['object']
    if compiled:
        if op!=SCHEDULE_OBJECT_ACTION or type(obj)is not str or obj not in OBJECT_REFERENCES:
            raise ValueError('Compiled delayed object must have one sealed source or counterpart reference')
    elif (not isinstance(obj,Mapping) or set(obj)!={'reference','logical_object_id'}
            or any(type(v)is not str or not v for v in obj.values())):
        raise ValueError('Delayed object needs its paired reference and incarnation')
    return effect


def delayed_object_node_capabilities(*,effects,target_schema,mechanic_ids):
    if DELAYED_OBJECT_MECHANIC not in mechanic_ids or target_schema is not None or len(effects)!=1:return ()
    try:effect=validate_delayed_object_instruction(effects[0],compiled=True)
    except (ValueError,TypeError,KeyError):return ()
    if effect['schema_version']!=1:return ()
    action_caps={'destroy':'permanent.destroy.effect','sacrifice':'choice.affected_player.fixed_sacrifice',
        'bounce':'permanent.return.owner_hand'}
    return (DELAYED_OBJECT_CAPABILITY,'trigger.placement.apnap','zone.change.destination_replacement',action_caps[effect['action']],
        *((DAMAGE_PERMANENT_CAPABILITY,) if effect['object']==DAMAGE_PERMANENT_OBJECT else ()))
