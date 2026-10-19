from __future__ import annotations

"""CR 103.6 actions between completed mulligans and the first turn."""

from dataclasses import replace
from typing import Any,Mapping

from .errors import GameRuleError
from .opening_hand_model import OpeningHandSubject,OpeningHandFrame,OpeningHandRevealed
from .card_program_faces import program_matches_face
from .card_programs.admission import program_has_complete_card_program_admission
from .semantic_runtime.action_permissions import (
    ActionPermissionKind,ActionPermissionSourceContext,default_action_permission_registry,
    OPENING_HAND_BATTLEFIELD_HANDLER_ID,
)
from .opening_reveal_model import OPENING_REVEAL_HANDLER
from .zone_transitions import ZoneTransitionOwner
from .replacement_effects import ReplacementChoiceRequired,ReplacementEffectError,replacement_choice_payload
from .replacement.immutable import FrozenMap,thaw_value
from .aura import AuraEntryChoiceRequired
from .enchant_spec import TypedEnchantSpec,AuraEnchantSubject



def _permissions(host,card,seat):
    if card.zone!='hand' or card.owner!=seat or card.face_down or card.is_token:
        return ()
    record=host.card_record(card)
    if record is None:return ()
    programs=tuple(host.semantics.runtime_handler_programs_for_oracle(record.oracle_id,
        active_zone='hand',event='action.permission'))
    handler_ids={OPENING_HAND_BATTLEFIELD_HANDLER_ID,OPENING_REVEAL_HANDLER}
    if not any(any(d.get('handler_id') in handler_ids for d in p.handlers) for p in programs):return ()
    registry=default_action_permission_registry()
    result=[]
    for program in programs:
        if (not host.semantic_program_is_current_trusted(program)
                or not program_has_complete_card_program_admission(program)
                or not program_matches_face(record,program,card)):continue
        for descriptor in program.handlers:
            if descriptor.get('handler_id') not in handler_ids:continue
            permissions=registry.lower(descriptor,ActionPermissionSourceContext(card.ref,card.logical_object_id,
                program.key,seat,tuple(host.active_seats)))
            result.extend(p for p in permissions if p.player==seat)
    return tuple(result)


def _permission_for_subject(host,card,subject):
    expected=ActionPermissionKind.OPENING_HAND_REVEAL if subject.action_kind=='reveal' else ActionPermissionKind.OPENING_HAND_BATTLEFIELD
    return next((p for p in _permissions(host,card,subject.player) if p.kind is expected
        and (not subject.semantic_key or p.source_semantic_key==subject.semantic_key)),None)


def begin_opening_hand_actions(host):
    if host.state.started:raise GameRuleError('Opening-hand actions cannot begin after the first turn')
    seats=tuple(host.state.turn_order)
    subjects=tuple(OpeningHandSubject(c.object_id,c.logical_object_id,c.ref,seat,
        'reveal' if p.kind is ActionPermissionKind.OPENING_HAND_REVEAL else 'battlefield',p.source_semantic_key)
        for seat in seats for oid in host.state.players[seat].zones['hand'] for c in (host.state.cards[oid],)
        for p in _permissions(host,c,seat))
    if not subjects:return False
    version=2 if any(s.action_kind=='reveal' for s in subjects) else 1
    if version==1:
        unique={s.object_id:replace(s,semantic_key='') for s in subjects};subjects=tuple(unique.values())
    frame=OpeningHandFrame(seats,subjects,version=version);frame.validate_state(host)
    _issue_next(host,frame)
    return True


def _current_subjects(host,frame):
    seat=frame.seats[frame.index]
    result=[]
    for subject in frame.subjects:
        if subject.player!=seat:continue
        card=host.state.cards.get(subject.object_id)
        if (card is None or card.logical_object_id!=subject.logical_object_id or card.ref!=subject.ref
                or card.object_id not in host.state.players[seat].zones['hand'] or _permission_for_subject(host,card,subject) is None):
            raise GameRuleError('An opening-hand source changed since its offer')
        result.append(subject)
    return tuple(result)


def _issue_next(host,frame):
    frame.validate_state(host)
    while frame.index<len(frame.seats):
        subjects=_current_subjects(host,frame)
        if subjects and host.state.players[frame.seats[frame.index]].in_game:break
        frame=replace(frame,subjects=tuple(s for s in frame.subjects if s.player!=frame.seats[frame.index]),index=frame.index+1)
    if frame.index==len(frame.seats):
        if frame.revealed:
            from .opening_reveal import finish_opening_reveals
            finish_opening_reveals(host,frame)
        host.turn_steps.start_first_turn()
        return
    seat=frame.seats[frame.index];refs=[s.ref for s in subjects]
    if frame.version==2:
        import hashlib
        options=[{'id':'finish','label':'Finish opening actions'}]
        for s in subjects:
            key='opening:'+hashlib.sha256((s.object_id+'|'+s.action_kind+'|'+s.semantic_key).encode('utf-8')).hexdigest()[:24]
            options.append({'id':key,'label':('Reveal ' if s.action_kind=='reveal' else 'Begin with ')+host.state.cards[s.object_id].printed_name})
        host.permissions.issue(kind='opening.hand',role='pilot',actors=[seat],allowed_actions=['choose'],
            payload_by_actor={seat:{'prompt':'Choose an opening action, or finish.','options':options,
                'legal_actions':[{'id':'choose','action':'choose','choice_schema':{'opening_action':{
                    'type':'enum','label':'Opening action','required':True,'legal_values':[o['id'] for o in options],
                    'default':'finish'}}}]}},continuation={'opening_hand':frame.to_dict()})
        return
    host.permissions.issue(kind='opening.hand',role='pilot',actors=[seat],allowed_actions=['choose'],
        payload_by_actor={seat:{'prompt':'Put one opening-hand card onto the battlefield, or finish.',
            'candidates':[{'id':s.ref,'name':host.state.cards[s.object_id].printed_name} for s in subjects],
            'legal_actions':[{'id':'choose','action':'choose','label':'Begin with selected card, or finish',
                'choice_schema':{'refs':{'type':'object_ref_array','shape':'ref_array','legal_refs':refs,
                    'label':'Opening-hand card','minimum':0,'maximum':1,'distinct':True}}}]}},
        continuation={'opening_hand':frame.to_dict()})


def complete_opening_hand_action(host,decision):
    if set(decision.continuation)!={'opening_hand'}:raise GameRuleError('Opening-hand continuation fields changed')
    frame=OpeningHandFrame.from_dict(decision.continuation['opening_hand']);frame.validate_state(host)
    if frame.index==len(frame.seats) or decision.actors!=[frame.seats[frame.index]]:
        raise GameRuleError('Opening-hand chooser changed')
    seat=decision.actors[0];response=decision.responses[seat]
    choices=response.get('choices',{})
    if not isinstance(choices,Mapping):raise GameRuleError('Opening-hand selection must be an object')
    if frame.version==2:
        value=choices.get('opening_action',response.get('opening_action'))
        if type(value) is not str:raise GameRuleError('Choose one offered opening action')
        subjects=_current_subjects(host,frame)
        if value=='finish':
            _issue_next(host,replace(frame,subjects=tuple(s for s in frame.subjects if s.player!=seat),index=frame.index+1))
            return
        import hashlib
        subject=next((s for s in subjects if value=='opening:'+hashlib.sha256(
            (s.object_id+'|'+s.action_kind+'|'+s.semantic_key).encode('utf-8')).hexdigest()[:24]),None)
        if subject is None:raise GameRuleError('The opening action was not offered to this player')
        if subject.action_kind=='reveal':
            from .opening_reveal import commit_opening_reveal
            permission=_permission_for_subject(host,host.state.cards[subject.object_id],subject)
            _issue_next(host,commit_opening_reveal(host,frame,subject,permission))
        else:_place(host,{'frame':frame.to_dict(),'subject':subject.to_dict(frame.version),'aura_target':None})
        return
    refs=choices.get('refs',response.get('refs',[]))
    if type(refs) is not list or len(refs)>1 or any(type(ref) is not str for ref in refs):
        raise GameRuleError('Choose at most one opening-hand card')
    subjects=_current_subjects(host,frame)
    if not refs:
        _issue_next(host,replace(frame,subjects=tuple(s for s in frame.subjects if s.player!=seat),index=frame.index+1))
        return
    subject=next((s for s in subjects if s.ref==refs[0]),None)
    if subject is None:raise GameRuleError('The selected card was not offered from this opening hand')
    _place(host,{'frame':frame.to_dict(),'subject':subject.to_dict(frame.version),'aura_target':None})


def _entry(value):
    if not isinstance(value,Mapping) or set(value)!={'frame','subject','aura_target'}:
        raise GameRuleError('Opening-hand entry fields changed')
    frame=OpeningHandFrame.from_dict(value['frame'])
    subject=OpeningHandSubject.from_dict(value['subject'],frame.version)
    if frame.index==len(frame.seats) or subject.action_kind!='battlefield' or subject not in frame.subjects or subject.player!=frame.seats[frame.index]:
        raise GameRuleError('Opening-hand entry no longer belongs to the chooser')
    if value['aura_target'] is not None and (type(value['aura_target']) is not str or not value['aura_target']):
        raise GameRuleError('Opening-hand Aura subject is malformed')
    return frame,subject


def _place(host,entry,selections=()):
    frame,subject=_entry(entry);frame.validate_state(host)
    if subject not in _current_subjects(host,frame):raise GameRuleError('Opening-hand entry source changed')
    try:
        with host.transaction():
            ZoneTransitionOwner(host).move_card(subject.object_id,'battlefield',controller=subject.player,
                aura_target_ref=entry['aura_target'],reason='opening-hand battlefield action',semantic_events=True,
                replacement_selections=selections)
    except ReplacementChoiceRequired as required:
        host.permissions.issue(kind='replacement.order',role='pilot',actors=[required.pending.choice.chooser],
            allowed_actions=['choose'],payload_by_actor={required.pending.choice.chooser:
                replacement_choice_payload(required.pending,required.effects)},
            continuation={'replacement_resume_kind':'opening_hand_entry','opening_hand_entry':dict(entry),
                'replacement_selections':[dict(v) if isinstance(v,Mapping) else v for v in selections],
                'replacement_batch':required.batch.to_dict(),'replacement_effects':[v.to_dict() for v in required.effects]})
        return
    except AuraEntryChoiceRequired as required:
        plan=required.plan;refs=list(plan.legal_target_refs)
        player_subject=isinstance(plan.spec,TypedEnchantSpec) and plan.spec.subject is AuraEnchantSubject.PLAYER
        host.permissions.issue(kind='aura.entry',role='pilot',actors=[plan.controller],allowed_actions=['choose'],
            payload_by_actor={plan.controller:{'prompt':'Choose a legal subject for this Aura to enchant.',
                'legal_actions':[{'id':'choose','action':'choose','choice_schema':{'aura_target':{
                    'type':'seat' if player_subject else 'object_ref','required':True,
                    ('legal_seats' if player_subject else 'legal_refs'):refs}}}]}},
            continuation={'opening_hand_entry':dict(entry),'replacement_selections':list(selections),
                'advertised_targets':refs,'controller':plan.controller})
        return
    current=host.state.cards.get(subject.object_id)
    unchanged_hand=current is not None and current.zone=='hand' and current.logical_object_id==subject.logical_object_id
    _issue_next(host,frame if unchanged_hand else replace(frame,subjects=tuple(s for s in frame.subjects
        if not (s.object_id==subject.object_id and s.logical_object_id==subject.logical_object_id))))


def decode_opening_hand_entry(continuation_type,value,batch,effects):
    from .replacement.replay import _decode_combat_selections
    try:
        frame,subject=_entry(value['opening_hand_entry'])
        if not batch.events or any(event.affected_object is None or event.affected_object.object_id!=subject.object_id
                for event in batch.events):
            raise GameRuleError('Opening-hand replacement batch belongs to a different source')
    except GameRuleError as exc:raise ReplacementEffectError(str(exc)) from exc
    return continuation_type(batch=batch,effects=effects,resume_kind='opening_hand_entry',
        opening_hand_entry=FrozenMap(value['opening_hand_entry']),
        replacement_selections=_decode_combat_selections(value))


def resume_opening_hand_replacement(host,restored,selection):
    if restored.opening_hand_entry is None:raise GameRuleError('Opening-hand entry frame disappeared')
    _place(host,thaw_value(restored.opening_hand_entry),
        tuple(thaw_value(v) if isinstance(v,FrozenMap) else v for v in restored.replacement_selections)+(selection,))


def resume_opening_hand_aura(host,decision):
    raw=decision.continuation
    if set(raw)!={'opening_hand_entry','replacement_selections','advertised_targets','controller'}:
        raise GameRuleError('Opening-hand Aura continuation fields changed')
    frame,subject=_entry(raw['opening_hand_entry']);frame.validate_state(host)
    controller=raw['controller']
    if type(controller) is not str or controller not in host.active_seats or decision.actors!=[controller]:
        raise GameRuleError('Opening-hand Aura chooser changed')
    refs=raw['advertised_targets']
    if type(refs) is not list or len(set(refs))!=len(refs) or any(type(ref) is not str for ref in refs):
        raise GameRuleError('Opening-hand Aura candidates are malformed')
    selected=decision.responses[controller].get('aura_target')
    if type(selected) is not str or selected not in refs:raise GameRuleError('Choose an advertised Aura subject')
    selections=raw['replacement_selections']
    if type(selections) is not list:raise GameRuleError('Opening-hand Aura replacement journal is malformed')
    _place(host,{**raw['opening_hand_entry'],'aura_target':selected},tuple(selections))
