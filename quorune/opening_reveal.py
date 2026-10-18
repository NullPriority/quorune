from __future__ import annotations

"""Reveal commitment and existing delayed-trigger scheduling for CR 103.6b."""

from dataclasses import replace
from .errors import GameRuleError
from .opening_hand_model import OpeningHandRevealed
from .effect_runtime.hand_inspection import begin_opening_hand_reveal,end_opening_hand_reveal
from .trigger_processing import schedule_delayed_trigger
from .replacement.immutable import thaw_value


def commit_opening_reveal(host,frame,subject,permission):
    spec=permission.opening_reveal_spec
    if spec is None:raise GameRuleError('Opening reveal lost its scheduled result')
    card=host.state.cards[subject.object_id]
    selected={'object_id':card.object_id,'logical_object_id':card.logical_object_id,'ref':card.ref,
        'player':subject.player,'oracle_id':card.oracle_id,'name':card.printed_name}
    prior=begin_opening_hand_reveal(host,selected)
    condition={'step':'main','phase':['precombat_main','postcombat_main'],'player':subject.player} if spec.timing=='your_first_main' else {
        'phase':'beginning','step':'upkeep',**({'player':subject.player} if spec.timing=='your_first_upkeep' else {})}
    schedule_delayed_trigger(host,controller=subject.player,label='Opening-hand result',event_kind='step.begin',
        condition=condition,source_object_id=subject.object_id,once=True,
        stack_template={'label':f'{card.printed_name} opening-hand result','context':{
            'dynamic_effects':[thaw_value(e) for e in spec.effects],
            'opening_reveal_source_program':permission.source_semantic_key}})
    remaining=tuple(s for s in frame.subjects if not (s.object_id==subject.object_id
        and s.logical_object_id==subject.logical_object_id and s.action_kind=='reveal'))
    return replace(frame,subjects=remaining,revealed=(*frame.revealed,OpeningHandRevealed(subject,prior)))


def finish_opening_reveals(host,frame):
    for record in frame.revealed:
        end_opening_hand_reveal(host,object_id=record.subject.object_id,
            logical_object_id=record.subject.logical_object_id,prior_revealed_to=record.prior_revealed_to)
