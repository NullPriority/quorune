from __future__ import annotations

"""Sealed gameplay occurrences from committed canonical counter placements."""

from collections import OrderedDict
from .replacement.immutable import FrozenMap,thaw_value
from .zone_trigger_events import sealed_public_characteristic_facts
from .trigger_processing import enqueue_trigger_batch
from .counter_placement_event_model import COUNTER_PLACEMENT_EVENT,SINGLE_COUNTER_EVENT,CounterPlacementOccurrence

def capture_counter_placement_occurrences(host,events,*,reason):
    """Capture after all mutations in the containing instruction are committed."""
    grouped=OrderedDict()
    for event in events:
        amount=event.payload.get('amount')
        if event.kind!='counter.place' or event.children or type(amount) is not int or amount<0:
            raise ValueError('Counter occurrences require resolved canonical placement leaves')
        if not amount or event.affected_object is None:
            continue
        object_id=event.affected_object.object_id
        card=host.state.cards.get(object_id)
        if card is None:
            raise ValueError('Counter placement occurrence subject is unavailable')
        if card.zone!='battlefield':
            continue
        identity=event.payload.get('target_logical_object_id')
        if identity is not None and identity!=card.logical_object_id:
            raise ValueError('Counter placement occurrence incarnation changed')
        counter=event.payload.get('counter_name');actor=event.payload.get('placing_player')
        if type(actor) is not str or actor not in host.active_seats:
            raise ValueError('Counter placement occurrence placing player is unavailable')
        key=(object_id,card.logical_object_id,counter,actor)
        if key not in grouped:
            grouped[key]={'card':card.ref,'card_object_identity':card.logical_object_id,
                'controller':card.controller,'owner':card.owner,'placing_player':actor,'counter':counter,
                'amount':0,'counter_event_ids':[],'reason':reason,
                'token':card.object_kind=='token','card_zone_change_counter':card.zone_change_counter,
                **sealed_public_characteristic_facts(host._effective_card_data(card))}
        grouped[key]['amount']+=amount
        grouped[key]['counter_event_ids'].append(event.event_id)
    occurrences=[];indices={}
    for context in grouped.values():
        key=(context['card_object_identity'],context['placing_player'])
        indices[key]=indices.get(key,0)+1
        context['counter_kind_index']=indices[key]
        context['event_id']='counter.put:'+':'.join(context['counter_event_ids'])
        occurrences.append(CounterPlacementOccurrence(FrozenMap(context)))
    return tuple(occurrences)


def counter_event_subscribers(host):
    """Use the existing printed-and-granted discovery boundary once per batch."""
    sources=host._semantic_event_sources(zones={'battlefield'})
    from .trigger_discovery import _event_programs_for_source,_selected_event_subscription
    selected={event:[] for event in (COUNTER_PLACEMENT_EVENT,SINGLE_COUNTER_EVENT)}
    for event in selected:
        for source in sources:
            _zone,_characteristics,programs=_event_programs_for_source(host,source,event,source_zones=None,source_characteristics=None)
            if any(_selected_event_subscription(program,event)[0] for program in programs):
                selected[event].append(source)
    return selected


def dispatch_counter_placement_occurrences(host,occurrences,*,trigger_batch=None,subscribers=None):
    if not occurrences:
        return
    owned=trigger_batch is None;batch=[] if owned else trigger_batch
    selected=counter_event_subscribers(host) if subscribers is None else subscribers
    if not any(selected.values()):
        return
    for occurrence in occurrences:
        context=thaw_value(occurrence.context)
        if selected[COUNTER_PLACEMENT_EVENT]:
            host._dispatch_semantic_event(COUNTER_PLACEMENT_EVENT,context,sources=selected[COUNTER_PLACEMENT_EVENT],trigger_batch=batch)
        # The singular event identity is private rules vocabulary, independent
        # from the once-per-object subscription; no result is multiplied later.
        for index in range(context['amount'] if selected[SINGLE_COUNTER_EVENT] else 0):
            host._dispatch_semantic_event(SINGLE_COUNTER_EVENT,{**context,'counter_index':index+1},
                sources=selected[SINGLE_COUNTER_EVENT],trigger_batch=batch)
    if owned:
        enqueue_trigger_batch(host,batch)


def dispatch_prepared_counter_events(host,prepared,*,reason,trigger_batch=None):
    if not any(event.affected_object is not None and type(event.payload.get('amount')) is int and event.payload['amount']>0 for event in prepared.events):
        return
    subscribers=counter_event_subscribers(host)
    if not any(subscribers.values()):
        return
    occurrences=capture_counter_placement_occurrences(host,prepared.events,reason=reason)
    dispatch_counter_placement_occurrences(host,occurrences,trigger_batch=trigger_batch,subscribers=subscribers)


def dispatch_counter_event_trees(host,resolutions,*,reason,trigger_batch=None):
    from .counter_placement import prepared_counter_events_from_tree
    events=[]
    for resolution in resolutions:
        if resolution.event is not None:
            events.extend(prepared_counter_events_from_tree(resolution.event,effects=resolution.effects,journal=resolution.journal).events)
    if not any(event.affected_object is not None and type(event.payload.get('amount')) is int and event.payload['amount']>0 for event in events):
        return
    subscribers=counter_event_subscribers(host)
    if not any(subscribers.values()):
        return
    occurrences=capture_counter_placement_occurrences(host,events,reason=reason)
    dispatch_counter_placement_occurrences(host,occurrences,trigger_batch=trigger_batch,subscribers=subscribers)
