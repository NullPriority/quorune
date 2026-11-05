"""Read current declaration membership; delegate occurrences to APNAP trigger owners."""
from ..unblocked_attacker_model import (UNBLOCKED_ATTACKER_EVENT,UNBLOCKED_ATTACKER_CAPABILITY,
    UnblockedAttackerOccurrence,unblocked_attacker_event_version)


def unblocked_attacker_occurrences(host):
    if unblocked_attacker_event_version(getattr(host.state,'unblocked_attacker_event_version',None)) is None:return ()
    combat=host.state.combat
    if not combat.blockers_declared:raise ValueError('Unblocked occurrences require completed blocker declarations')
    rows=[]
    for object_id,target in combat.attackers.items():
        if object_id in combat.blockers:continue
        card=host.state.cards.get(object_id)
        if card is None or card.zone!='battlefield' or card.phased_out:continue
        if 'creature'not in host._type_parts(str(host._effective_card_data(card).get('type_line')or''))[0]:continue
        defender=host._defending_player_for_attacker(object_id,target)
        if defender not in host.active_seats:continue
        rows.append(UnblockedAttackerOccurrence(card.ref,card.object_id,card.logical_object_id,card.controller,defender))
    return tuple(sorted(rows,key=lambda r:(r.reference,r.logical_object_id)))


def unblocked_attacker_trigger_items(host,*,rows=None):
    if rows is None:rows=unblocked_attacker_occurrences(host)
    if not rows:return [],()
    sources=tuple(host._semantic_event_sources(zones={'battlefield'}));items=[];refs=[]
    for row in rows:refs.extend(host._dispatch_semantic_event(UNBLOCKED_ATTACKER_EVENT,row.semantic_context(),sources=sources,trigger_batch=items))
    return items,tuple(refs)


def unblocked_attacker_runtime_dependencies(program):
    from .event_subscriptions import FixedEventSubscriptionSet
    subscriptions=FixedEventSubscriptionSet.from_condition(program.event_condition)
    events={s.event.removesuffix('.self') for s in subscriptions.subscriptions} if subscriptions else {program.event.removesuffix('.self')}
    return (UNBLOCKED_ATTACKER_CAPABILITY,) if UNBLOCKED_ATTACKER_EVENT in events else ()
