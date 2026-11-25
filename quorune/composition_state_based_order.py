"""Authenticate physical order choices before one state-based departure batch."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace

from .composition_batch_order_model import CompositionStateBasedOrderContinuation
from .composition_departure_batch import prepare_composition_departure_batch
from .errors import GameRuleError
from .replacement.immutable import FrozenMap
from .semantic_choices.physical_ordering import physical_order_payload as _payload, physical_order_response
from .zone_trigger_events import ZoneTransitionKind

_ORDERS=ContextVar('state_based_composition_orders',default=None)
_PREPARED=ContextVar('prepared_state_based_composition_batch',default=None)


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
    from .state_based_legend_choices import state_based_legend_choices
    if set(decision.continuation)!={'composition_state_based_order'}:
        raise GameRuleError('Physical departure continuation fields changed')
    try:frame=CompositionStateBasedOrderContinuation.from_dict(decision.continuation['composition_state_based_order'])
    except (ValueError,TypeError,KeyError) as exc:raise GameRuleError(str(exc)) from exc
    with state_based_legend_choices(host,frame.state_based_frame.get('legend_choices')):
        _complete_state_based_composition_order(host,decision,frame)


def _complete_state_based_composition_order(host,decision,frame):
    from .state_based_replacement_coordination import state_based_replacement_frame,state_based_replacement_selections
    if (frame.state_based_frame!=FrozenMap(state_based_replacement_frame(host))
            or frame.request.apnap_order!=tuple(host.apnap_order())):
        raise GameRuleError('State-based physical inputs changed before completion')
    refs = physical_order_response(host, decision, frame)
    try:updated=frame.with_order(refs)
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
