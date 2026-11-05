"""Public committed damage-recipient identity, independent of its source."""
from collections.abc import Mapping

DAMAGE_PERMANENT_OBJECT='$event.damaged_permanent'
DAMAGE_PERMANENT_CONTEXT='damage_permanent_recipient'
DAMAGE_PERMANENT_CAPABILITY='trigger.event.damage_permanent_recipient'
DAMAGE_PERMANENT_VARIANT='dealt_creature_damage_recipient'


def uses_damaged_permanent(value):
    if isinstance(value,Mapping):return any(uses_damaged_permanent(child) for child in value.values())
    if isinstance(value,(list,tuple)):return any(uses_damaged_permanent(child) for child in value)
    return value==DAMAGE_PERMANENT_OBJECT


def capture_damaged_permanent(event,context):
    if (event!='damage.dealt' or context.get('target_kind')!='permanent'
            or not isinstance(context.get('target_types'),(list,tuple)) or 'creature'not in context['target_types']
            or type(context.get('amount'))is not int or context['amount']<=0
            or any(type(context.get(key))is not str or not context[key]
                for key in ('target','target_object_id','target_logical_object_id'))):
        raise ValueError('Damaged creature requires a positive committed permanent-recipient event')
    return {DAMAGE_PERMANENT_CONTEXT:{'reference':context['target'],
        'logical_object_id':context['target_logical_object_id']}}
