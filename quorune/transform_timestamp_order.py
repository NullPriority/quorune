from __future__ import annotations

"""Authenticated timestamp ordering through the existing decision lifecycle."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace

from .errors import GameRuleError
from .replacement.immutable import FrozenMap
from .semantic_choices.model import OrderingChoice
from .transform_order_model import TransformTimestampContinuation,TransformTimestampRequest,TransformTimestampOrderRequired

_SELECTED=ContextVar('selected_transform_timestamp_order',default=None)
_CONTEXT_FIELDS=('active_player','turn_sequence','phase_index','phase','step')


def select_transform_timestamp_cards(host,cards,*,reason,day_night_instruction,expected_counts):
    request=TransformTimestampRequest(tuple(FrozenMap(card.to_dict()) for card in cards),reason,
        day_night_instruction,FrozenMap({card.object_id:expected_counts[card.object_id]
            for card in cards if card.object_id in expected_counts}))
    selected=_SELECTED.get()
    if selected is not None and not selected[2]:
        saved,ids,_used=selected
        if saved!=request:raise GameRuleError('Transform timestamp batch changed before resumption')
        _SELECTED.set((saved,ids,True))
        by_id={card.object_id:card for card in cards}
        return tuple(by_id[object_id] for object_id in ids)
    if request.choices:raise TransformTimestampOrderRequired(request)
    return tuple(cards)


def _schema(refs):
    return OrderingChoice(field_name='cards',legal_refs=refs,visibility='actor_private',
        schema_extras=FrozenMap({'type':'object_ref_array','shape':'ref_array','order':'earliest_first'})).choice_schema()


def _choice_payload(frame):
    choice=frame.current_choice
    if choice is None:raise GameRuleError('Transform timestamp order is already complete')
    actor,refs=choice
    names={value['ref']:value['printed_name'] for value in frame.request.subjects}
    return {'prompt':'Order your permanents by timestamp, earliest first.',
        'cards':[{'id':ref,'name':names[ref]} for ref in refs],
        'previous_orders':[{'controller':prior_actor,'cards':list(order)}
            for (prior_actor,_refs),order in zip(frame.request.choices,frame.orders)],
        'legal_actions':[{'id':'order','action':'order','label':'Confirm timestamp order','choice_schema':_schema(refs)}]}


def _issue(host,frame):
    choice=frame.current_choice
    if choice is None:raise GameRuleError('Transform timestamp order is already complete')
    actor,_refs=choice
    if actor not in host.active_seats:raise GameRuleError('Transform timestamp controller is not a participant')
    host.permissions.issue(kind='permanent.timestamp_order',role='pilot',actors=[actor],allowed_actions=['order'],
        payload_by_actor={actor:_choice_payload(frame)},
        continuation={'transform_timestamp_order':frame.to_dict()})


def begin_transform_timestamp_order(host,request,*,resume_kind,held_triggers=()):
    frame=TransformTimestampContinuation(request,FrozenMap({key:getattr(host.state,key) for key in _CONTEXT_FIELDS}),
        resume_kind,host.state.priority_player,tuple(FrozenMap(item.to_dict()) for item in held_triggers))
    _issue(host,frame)


def _validate_context(host,frame):
    if frame.context!=FrozenMap({key:getattr(host.state,key) for key in _CONTEXT_FIELDS}):
        raise GameRuleError('Transform timestamp turn or step changed')
    if (frame.priority_player is not None and frame.priority_player not in host.state.players
            or frame.context['active_player'] is not None and frame.context['active_player'] not in host.state.players):
        raise GameRuleError('Transform timestamp continuation names an unknown player')


@contextmanager
def _selected_order(frame):
    token=_SELECTED.set((frame.request,frame.ordered_ids,False))
    try:
        yield
        if not _SELECTED.get()[2]:raise GameRuleError('Transform timestamp instruction did not resume')
    finally:_SELECTED.reset(token)


def _resolution_item(host, retained):
    from .semantic_choices.model import SemanticChoiceFrame
    from .card_overrides import normalize_game_record_v3_effect
    from .rules.subtype_transform import SubtypeTransformSpec
    SubtypeTransformSpec.from_effect(retained['effect'])
    semantic = SemanticChoiceFrame.from_dict(retained['semantic_frame'])
    item = next((item for item in host.state.stack if item.ref==retained['stack_ref']),None)
    if item is None or item.controller!=semantic.controller:
        raise GameRuleError('Transform ordering resolving stack object changed')
    host._validate_semantic_frame(semantic.to_dict(),item)
    program=host.semantics.get(item.semantic_key)
    pointer=semantic.instruction_pointer
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not 0<=pointer<len(program.effects)):
        raise GameRuleError('Transform ordering requires its current trusted parent instruction')
    expected=normalize_game_record_v3_effect(host._semantic_value(program.effects[pointer],item))
    if FrozenMap(expected)!=FrozenMap(retained['effect']):
        raise GameRuleError('Transform ordering parent effect differs from its trusted instruction')
    return item,semantic


def issue_transform_resolution_order(host,item,required,effect,remaining,destination,note,instruction_pointer):
    retained=FrozenMap({'stack_ref':item.ref,'effect':dict(effect),'remaining':list(remaining),
        'destination':destination,'note':note,
        'semantic_frame':host._semantic_frame(item,instruction_pointer=instruction_pointer)})
    _resolution_item(host,retained)
    frame=TransformTimestampContinuation(required.request,
        FrozenMap({key:getattr(host.state,key) for key in _CONTEXT_FIELDS}),
        'resolution',host.state.priority_player,(),resolution=retained)
    _issue(host,frame)


def _resume(host,frame,*,selected):
    from .model import StackItem
    from .replacement.immutable import thaw_value
    _validate_context(host,frame)
    triggers=[StackItem.from_dict(thaw_value(value)) for value in frame.held_triggers
        if value['controller'] in host.active_seats]
    def run():
        if frame.resume_kind=='untap':
            from .untap_step_coordination import coordinate_untap_step
            coordinate_untap_step(host,phase=frame.context['phase'],step=frame.context['step'],
                active_player=frame.context['active_player'],held_triggers=triggers,phasing_complete=True)
        elif frame.resume_kind=='resolution':
            from .replacement.immutable import thaw_value
            retained=frame.resolution
            if not selected and not any(item.ref==retained['stack_ref'] for item in host.state.stack):
                if not host._stabilize():host._grant_priority(host.state.active_player)
                return
            item,semantic=_resolution_item(host,retained)
            host._continue_resolution(stack_ref=item.ref,
                effects=[thaw_value(retained['effect']),*(thaw_value(e) for e in retained['remaining'])],
                destination=retained['destination'],note=retained['note'],
                instruction_pointer=semantic.instruction_pointer)
        elif not host._stabilize():
            host._grant_priority(frame.priority_player or host.state.active_player)
    if selected:
        with _selected_order(frame):run()
    else:run()


def complete_transform_timestamp_order(host,decision):
    if set(decision.continuation)!={'transform_timestamp_order'}:
        raise GameRuleError('Transform timestamp continuation is malformed')
    frame=TransformTimestampContinuation.from_dict(decision.continuation['transform_timestamp_order'])
    _validate_context(host,frame);frame.request.validate_current(host)
    choice=frame.current_choice
    if choice is None:raise GameRuleError('Transform timestamp choice is already complete')
    actor,refs=choice
    if (decision.kind!='permanent.timestamp_order' or decision.role!='pilot' or decision.actors!=[actor]
            or actor not in host.active_seats or set(decision.responses)!={actor}):
        raise GameRuleError('Transform timestamp ordering principal changed')
    if FrozenMap(decision.payload_by_actor)!=FrozenMap({actor:_choice_payload(frame)}):
        raise GameRuleError('Transform timestamp issued payload changed')
    response=decision.responses[actor]
    if set(response)-{'action','cards','choice_schema','label'} or response.get('action')!='order':
        raise GameRuleError('Transform timestamp response has unsupported fields')
    if 'label' in response and response['label']!='Confirm timestamp order':
        raise GameRuleError('Transform timestamp delivered label changed')
    if 'choice_schema' in response and FrozenMap(response['choice_schema'])!=FrozenMap(_schema(refs)):
        raise GameRuleError('Transform timestamp delivered schema changed')
    updated=frame.with_order(response.get('cards'))
    if updated.current_choice is not None:_issue(host,updated)
    else:_resume(host,updated,selected=True)


def reconcile_transform_timestamp_order_after_departure(host):
    decision=host.state.pending_decision
    if decision is None or decision.kind!='permanent.timestamp_order':return
    frame=TransformTimestampContinuation.from_dict(decision.continuation.get('transform_timestamp_order'))
    host.permissions.invalidate_current()
    if not host.state.game_over:_resume(host,replace(frame,orders=()),selected=False)
