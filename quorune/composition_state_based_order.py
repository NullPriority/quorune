"""Authenticate physical order choices before one state-based departure batch."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace

from .composition_batch_order_model import CompositionStateBasedOrderContinuation
from .composition_departure_batch import prepare_composition_departure_batch
from .errors import GameRuleError
from .replacement.immutable import FrozenMap
from .semantic_choices.model import OrderingChoice
from .zone_trigger_events import ZoneTransitionKind

_ORDERS=ContextVar('state_based_composition_orders',default=None)
_PREPARED=ContextVar('prepared_state_based_composition_batch',default=None)


def _schema(refs):
    return OrderingChoice('cards',refs,visibility='actor_private',
        schema_extras=FrozenMap({'type':'object_ref_array','shape':'ref_array','order':'bottom_to_top'})).choice_schema()


def _payload(frame):
    choice=frame.current_choice
    if choice is None:raise GameRuleError('Physical departure ordering is already complete')
    (actor,destination,_position),refs=choice
    states={state['ref']:state for subject in frame.request.subjects for state in subject.physical_states}
    def name(ref):
        state=states[ref]
        return ('Face-down component' if destination=='library' and state['face_down']
            and actor not in (*state['known_to'],*state['revealed_to']) else state['printed_name'])
    previous=[{'player':group[0],'destination':group[1],'cards':list(order)}
        for (group,_refs),order in zip(frame.request.choices,frame.orders) if group[1]!='library']
    return {'prompt':'Order the departing physical cards from bottom to top.',
        'cards':[{'id':ref,'name':name(ref)} for ref in refs],'previous_orders':previous,
        'legal_actions':[{'id':'order','action':'order','label':'Confirm physical order','choice_schema':_schema(refs)}]}


def _issue(host,frame):
    (actor,_destination,_position),_refs=frame.current_choice
    if actor not in host.active_seats:raise GameRuleError('Physical ordering principal is not a surviving participant')
    host.permissions.issue(kind='permanent.composition_order',role='pilot',actors=[actor],allowed_actions=['order'],
        payload_by_actor={actor:_payload(frame)},continuation={'composition_state_based_order':frame.to_dict()})


def prepare_state_based_compositions(host,execution,selections,zone_selections):
    from .state_based_replacement_coordination import state_based_replacement_frame
    from .zone_transitions import ZoneTransitionOwner
    destruction=execution.destruction
    ordered=destruction.destruction_event_order or destruction.destroyed_object_ids
    changes=tuple((oid,'graveyard') for oid in ordered)+execution.destruction_companions
    if not any(host.state.cards[oid].permanent_composition is not None for oid,_dest in changes):return execution
    batch=prepare_composition_departure_batch(ZoneTransitionOwner(host),changes,reason=destruction.reason,
        transition_kinds={oid:ZoneTransitionKind.SACRIFICE for oid in execution.saga_sacrifices},
        prepared_replacements=dict(execution.zone_replacements))
    current=FrozenMap(state_based_replacement_frame(host))
    selected=_ORDERS.get()
    if selected is not None and not selected[1]:
        frame,_used=selected
        if frame.request!=batch.request or frame.state_based_frame!=current:
            raise GameRuleError('State-based physical batch changed before resumption')
        _ORDERS.set((frame,True))
        return replace(execution,composition_departure_batch=batch.with_orders(frame.orders))
    if not batch.request.choices:return replace(execution,composition_departure_batch=batch.with_orders(()))
    _issue(host,CompositionStateBasedOrderContinuation(batch.request,current,tuple(selections),tuple(zone_selections)))
    return None


@contextmanager
def prepared_state_based_compositions(batch):
    if batch is None:
        yield;return
    token=_PREPARED.set((batch,False))
    try:
        yield
        if not _PREPARED.get()[1]:raise GameRuleError('Prepared physical state-based batch was not committed')
    finally:_PREPARED.reset(token)


def take_prepared_composition_departures(changes,reason,kinds,prepared_replacements):
    current=_PREPARED.get()
    if current is None or current[1]:return None
    batch,_used=current
    if (tuple(changes)!=batch.changes or reason!=batch.reason
            or dict(prepared_replacements or {})!={group.plan.card.object_id:group.plan.prepared_replacement for group in batch.groups}
            or any(kinds.get(group.plan.card.object_id,ZoneTransitionKind.ORDINARY).value!=group.subject.transition_kind
                for group in batch.groups)):
        raise GameRuleError('Physical commitment does not match its prepared simultaneous batch')
    _PREPARED.set((batch,True))
    return batch


def complete_state_based_composition_order(host,decision):
    from .state_based_replacement_coordination import state_based_replacement_frame,state_based_replacement_selections
    if set(decision.continuation)!={'composition_state_based_order'}:
        raise GameRuleError('Physical departure continuation fields changed')
    try:frame=CompositionStateBasedOrderContinuation.from_dict(decision.continuation['composition_state_based_order'])
    except (ValueError,TypeError,KeyError) as exc:raise GameRuleError(str(exc)) from exc
    if (frame.state_based_frame!=FrozenMap(state_based_replacement_frame(host))
            or frame.request.apnap_order!=tuple(host.apnap_order())):
        raise GameRuleError('State-based physical inputs changed before completion')
    choice=frame.current_choice
    if choice is None:raise GameRuleError('Physical ordering is already complete')
    (actor,_destination,_position),refs=choice
    if (decision.kind!='permanent.composition_order' or decision.role!='pilot' or decision.actors!=[actor]
            or actor not in host.active_seats or set(decision.responses)!={actor}
            or FrozenMap(decision.payload_by_actor)!=FrozenMap({actor:_payload(frame)})):
        raise GameRuleError('Physical ordering principal or issued payload changed')
    response=decision.responses[actor]
    if (set(response)-{'action','cards','choice_schema','label'} or response.get('action')!='order'
            or 'label' in response and response['label']!='Confirm physical order'
            or 'choice_schema' in response and FrozenMap(response['choice_schema'])!=FrozenMap(_schema(refs))):
        raise GameRuleError('Physical order response differs from its issued schema')
    try:updated=frame.with_order(response.get('cards'))
    except (ValueError,TypeError) as exc:raise GameRuleError(str(exc)) from exc
    if updated.current_choice is not None:
        _issue(host,updated);return
    token=_ORDERS.set((updated,False))
    try:
        with state_based_replacement_selections(updated.destruction_selections,updated.zone_selections):
            waiting=host._stabilize()
        if not _ORDERS.get()[1]:raise GameRuleError('Physical ordering did not resume its state-based batch')
    finally:_ORDERS.reset(token)
    if not waiting:host._grant_priority(host.state.active_player)
