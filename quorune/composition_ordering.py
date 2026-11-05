from __future__ import annotations

"""Scoped order selection while the original resolving effect is retried."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from .composition_order_model import CompositionOrderRequest,CompositionOrderChoiceRequired
from .errors import GameRuleError
from .replacement.immutable import FrozenMap
from .zone_trigger_events import ZoneTransitionKind

_ACTOR:ContextVar[str|None]=ContextVar('composition_effect_actor',default=None)
_SELECTION:ContextVar[object]=ContextVar('composition_selected_order',default=None)


@contextmanager
def composition_resolution_actor(actor):
    token=_ACTOR.set(actor)
    try:yield
    finally:_ACTOR.reset(token)


@contextmanager
def selected_composition_order(request,refs):
    request.validate_order(refs)
    token=_SELECTION.set((request,tuple(refs)))
    try:yield
    finally:_SELECTION.reset(token)


def resolve_composition_departure(owner,object_id,destination,*,reason,position='top',
        replacement_selections=(),transition_kind=ZoneTransitionKind.ORDINARY,prepared_replacement=None,
        damage_clears=()):
    from .composition_departure_commit import _prepare
    actor=_ACTOR.get()
    if actor is None:raise GameRuleError('Composition departure has no represented resolving-effect actor')
    root=owner.state.cards.get(object_id)
    if root is None or root.permanent_composition is None:raise GameRuleError('Composed departure source disappeared')
    _root,_storage,plan=_prepare(owner,object_id,destination,root.permanent_composition.topology.physical_object_ids,
        None,actor,reason,position,replacement_selections,transition_kind,prepared_replacement)
    if plan is None:return ()
    from .composition_replacements import composition_ordering_principal
    chooser=composition_ordering_principal(owner.host,root,plan.prepared_replacement,actor)
    from .composition_replacements import composition_component_destinations
    redirects,actors=composition_component_destinations(owner.host,root,plan.prepared_replacement,actor)
    if actors:
        from .composition_batch_order_model import CompositionDepartureSubject,CompositionDepartureBatchRequest
        subject=CompositionDepartureSubject(FrozenMap(root.to_dict()),destination,plan.destination,chooser,
            plan.library_position or 'top',transition_kind.value,redirects,actors)
        batch=CompositionDepartureBatchRequest((subject,),tuple(owner.host.apnap_order()))
        if not batch.choices:
            return owner.depart_composition(object_id,destination,physical_order=batch.ordered_ids(()),
                chooser=chooser,actor=actor,reason=reason,position=position,replacement_selections=replacement_selections,
                transition_kind=transition_kind,prepared_replacement=prepared_replacement,damage_clears=damage_clears)
        ordering_actor=batch.choices[0][0][0]
        request=CompositionOrderRequest(root.to_dict(),destination,actor,ordering_actor,reason,
            plan.library_position or 'top',transition_kind.value,4,plan.destination,ordering_actor,
            batch_request=batch)
        selection=_SELECTION.get()
        if selection is None:raise CompositionOrderChoiceRequired(request)
        selected,refs=selection
        if replace(selected,chooser=ordering_actor,ordering_actor=ordering_actor,orders=())!=request:
            raise GameRuleError('Composition selection belongs to a different or stale departure')
        selected.validate_order(refs)
        orders=(*selected.orders,tuple(refs))
        if len(orders)<len(batch.choices):
            next_actor=batch.choices[len(orders)][0][0]
            raise CompositionOrderChoiceRequired(replace(request,chooser=next_actor,ordering_actor=next_actor,orders=orders))
        return owner.depart_composition(object_id,destination,physical_order=batch.ordered_ids(orders),
            chooser=chooser,actor=actor,reason=reason,position=position,replacement_selections=replacement_selections,
            transition_kind=transition_kind,prepared_replacement=prepared_replacement,damage_clears=damage_clears)
    changed=plan.destination!=destination or chooser!=(root.owner if destination in ('graveyard','library','hand','command') else actor)
    from .composition_commander_replacement_model import commander_component_destinations
    redirects=FrozenMap(commander_component_destinations(plan.prepared_replacement.event.payload))
    version=3 if redirects else 2 if changed else 1
    request=CompositionOrderRequest(root.to_dict(),destination,actor,chooser,reason,
        plan.library_position or 'top',transition_kind.value,version,
        plan.destination if version in {2,3} else None,chooser if version in {2,3} else None,redirects)
    if plan.destination in {'hand','command','outside'} or len(request.refs)<=1:
        return owner.depart_composition(object_id,destination,physical_order=request.ordered_ids(request.refs),
            chooser=chooser,actor=actor,reason=reason,position=position,
            replacement_selections=replacement_selections,transition_kind=transition_kind,prepared_replacement=prepared_replacement,damage_clears=damage_clears)
    selection=_SELECTION.get()
    if selection is None:raise CompositionOrderChoiceRequired(request)
    selected,refs=selection
    if selected!=request:raise GameRuleError('Composition selection belongs to a different or stale departure')
    return owner.depart_composition(object_id,destination,physical_order=request.ordered_ids(refs),
        chooser=chooser,actor=actor,reason=reason,position=position,
        replacement_selections=replacement_selections,transition_kind=transition_kind,
        prepared_replacement=prepared_replacement,damage_clears=damage_clears)


def resolve_composition_departure_batch(owner,changes,*,reason,replacement_selections,transition_kinds,
        prepared_replacements,damage_clears=()):
    from .zone_trigger_events import normalized_transition_kind_map
    from .composition_state_based_order import take_prepared_composition_departures
    from .composition_departure_commit import commit_prepared_composition_departures
    kinds=normalized_transition_kind_map(changes,transition_kinds)
    physical=take_prepared_composition_departures(changes,reason,kinds,prepared_replacements)
    if physical is not None:
        return list(commit_prepared_composition_departures(owner,physical,damage_clears=damage_clears))
    if len(changes)!=1:raise GameRuleError('Composed multi-object batches require their represented group continuation')
    object_id,destination=changes[0]
    return list(resolve_composition_departure(owner,object_id,destination,reason=reason,
        replacement_selections=replacement_selections,transition_kind=kinds.get(object_id,ZoneTransitionKind.ORDINARY),
        prepared_replacement=(prepared_replacements or {}).get(object_id),damage_clears=damage_clears))


def validate_original_composition_effect(host,item,effect,instruction_pointer):
    from .card_overrides import normalize_game_record_v3_effect
    program=host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or type(instruction_pointer) is not int or not 0<=instruction_pointer<len(program.effects)):
        raise GameRuleError('Composition ordering requires its current trusted parent instruction')
    expected=normalize_game_record_v3_effect(host._semantic_value(program.effects[instruction_pointer],item))
    retained={key:value for key,value in effect.items() if key!='_replacement_selections'}
    if FrozenMap(expected)!=FrozenMap(retained):
        raise GameRuleError('Composition parent effect does not match its trusted instruction')


def issue_composition_order_choice(host,item,required,original_effect,remaining,destination,note,instruction_pointer):
    validate_original_composition_effect(host,item,original_effect,instruction_pointer)
    host._begin_registered_semantic_choice(item=item,
        effect={'op':'composition_departure_order','player':required.request.chooser,
            'request':required.request.to_dict(),'original_effect':dict(original_effect)},
        remaining=remaining,destination=destination,note=note,instruction_pointer=instruction_pointer)


def suspend_composition_completion(host,item,continuation,completion,index,intent,required):
    from .semantic_runtime.composition_intent import ResumeCompositionOrderIntent
    if (not isinstance(intent,ResumeCompositionOrderIntent) or index!=0 or len(completion.intents)!=1
            or completion.prepend_effects or completion.repeat_effect is not None
            or continuation.effect.get('op')!='composition_departure_order'):
        raise GameRuleError('Physical ordering can suspend only its exact single resumed instruction')
    issue_composition_order_choice(host,item,required,intent.original_effect,continuation.remaining,
        continuation.destination,continuation.note,continuation.semantic_frame.instruction_pointer)
    return False


def resume_composition_order(host,intent):
    from .semantic_runtime import default_semantic_interpreter,execute_intent_plan
    from .semantic_runtime.context import semantic_source_context
    request=intent.request;request.validate_current(host.state)
    frame=intent.semantic_frame
    item=next((item for item in host.state.stack if item.ref==frame['stack_object']),None)
    if item is None or item.controller!=request.actor:raise GameRuleError('Composition ordering parent controller changed')
    host._validate_semantic_frame(frame,item)
    validate_original_composition_effect(host,item,intent.original_effect,frame['instruction_pointer'])
    with composition_resolution_actor(request.actor),selected_composition_order(request,intent.ordered_refs):
        plan=default_semantic_interpreter().lower_for_seats(intent.original_effect,actor=item.controller,
            default_reason=item.label,seats=host.seats,active_seats=host.active_seats,apnap_order=host.apnap_order(),
            source=semantic_source_context(item,host.state.cards,instruction_pointer=frame['instruction_pointer']))
        if plan is None:return host.apply_effect(dict(intent.original_effect),actor=item.controller,as_cost=False)
        return execute_intent_plan(host,plan)
