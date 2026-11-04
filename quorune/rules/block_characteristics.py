"""Seal all block participants before ordinary relationship-trigger discovery."""
from ..block_characteristic_model import (BLOCK_CHARACTERISTIC_CONTEXT,BLOCK_CHARACTERISTIC_CONDITION,
    BLOCK_CHARACTERISTIC_CAPABILITY,BLOCK_FACT_COLLECTIONS,block_characteristics_version,
    validate_block_characteristics,block_counterpart_query)
from collections.abc import Mapping
from ..object_query import ObjectQueryResult,object_matches_query


def block_characteristic_contexts(host,event):
    if block_characteristics_version(getattr(host.state,'block_characteristics_version',None)) is None:return {}
    result={}
    for participant in event.participants:
        card=host.state.cards.get(participant.object_id)
        if card is None or card.logical_object_id!=participant.logical_object_id:
            raise ValueError('Block participant incarnation changed before fact capture')
        row=host._public_object_query_result(card)
        facts={'schema_version':1,'object_id':row.object_id,'reference':row.ref,'logical_object_id':row.logical_object_id,
            'owner':row.owner,'controller':row.controller,'power':row.effective_power,'toughness':row.effective_toughness,
            **{name:list(dict.fromkeys(getattr(row,name))) for name in BLOCK_FACT_COLLECTIONS}}
        validate_block_characteristics(facts);result[participant.object_id]=facts
    return result


def qualified_block_runtime_dependencies(program):
    found=False
    def visit(value):
        nonlocal found
        if isinstance(value,Mapping):
            if value.get('field')==BLOCK_CHARACTERISTIC_CONDITION:
                if set(value)!={'field','op','value','query'} or value['op']!='eq' or value['value']is not True:
                    raise ValueError('Qualified block condition must retain its immutable query')
                block_counterpart_query(value['query']);found=True
            for child in value.values():visit(child)
        elif isinstance(value,(list,tuple)):
            for child in value:visit(child)
    visit(program.event_condition)
    if not found:return ()
    from .event_subscriptions import FixedEventSubscriptionSet
    subscriptions=FixedEventSubscriptionSet.from_condition(program.event_condition)
    events={child.event for child in subscriptions.subscriptions} if subscriptions else {program.event}
    if not events or not events<={'creature.blocks','creature.becomes_blocked_by'} or program.active_zone!='battlefield':
        raise ValueError('Qualified block predicates require actual per-pair block events')
    return (BLOCK_CHARACTERISTIC_CAPABILITY,)


def block_counterpart_matches(condition,context):
    if (not isinstance(condition,Mapping) or set(condition)!={'field','op','value','query'}
            or condition['field']!=BLOCK_CHARACTERISTIC_CONDITION or condition['op']!='eq' or condition['value']is not True):
        raise ValueError('Block counterpart condition is malformed')
    query=block_counterpart_query(condition['query'])
    facts=context.get(BLOCK_CHARACTERISTIC_CONTEXT)
    if facts is None:return False
    validate_block_characteristics(facts)
    from ..block_transitions import BlockTransitionEvent
    event=context.get('block_pair_event')
    if event not in {'creature.blocks','creature.becomes_blocked_by'}:raise ValueError('Block counterpart needs its actual relationship event')
    transition=BlockTransitionEvent.from_dict(context.get('block_transition'))
    participants={p.reference:p for p in transition.participants}
    other=context.get('blocked_attacker') if event=='creature.blocks' else context.get('blocking_creature')
    if other!=facts['reference']:raise ValueError('Sealed block facts differ from the actual counterpart')
    source=participants.get(context.get('card'));counterpart=participants.get(other)
    if source is None or counterpart is None or (counterpart.object_id,counterpart.logical_object_id)!=(facts['object_id'],facts['logical_object_id']):
        raise ValueError('Block facts are not bound to a sealed participant incarnation')
    attacker,blocker=(counterpart,source) if event=='creature.blocks' else (source,counterpart)
    if not any(a.attacker_object_id==attacker.object_id and a.blocker_object_id==blocker.object_id for a in transition.assignments):
        raise ValueError('Block facts are not bound to the selected pair')
    row=ObjectQueryResult(facts['object_id'],facts['reference'],'Blocking participant',facts['owner'],facts['controller'],'battlefield',
        types=tuple(facts['types']),subtypes=tuple(facts['subtypes']),supertypes=tuple(facts['supertypes']),colors=tuple(facts['colors']),
        keywords=tuple(facts['keywords']),effective_power=facts['power'],effective_toughness=facts['toughness'],logical_object_id=facts['logical_object_id'])
    return object_matches_query(row,query)
