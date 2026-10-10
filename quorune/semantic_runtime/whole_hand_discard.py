from __future__ import annotations

"""Typed whole-hand discard lowering in the canonical semantic registry."""

from dataclasses import dataclass
from ..whole_hand_discard_model import (
    WHOLE_HAND_DISCARD_OPERATION,WHOLE_HAND_DISCARD_CAPABILITY,DiscardWholeHandsIntent,
)
from .context import SemanticNodeError
from .intents import IntentPlan,MoveObjectsSimultaneouslyIntent


@dataclass(frozen=True,slots=True)
class WholeHandDiscardHandler:
    operation:str=WHOLE_HAND_DISCARD_OPERATION
    handler_id:str='generic.discard-whole-hands.v1'
    schema_version:int=1
    family:str='zone.discard.whole_hand'
    rule_references:tuple[str,...]=('402.1','608.2c','608.2h','701.9a','701.9c')
    capability_dependencies:tuple[str,...]=(WHOLE_HAND_DISCARD_CAPABILITY,)

    def lower(self,effect,context):
        if set(effect)-{'op','actor','players','reason','_replacement_selections'} or not {'op','actor','players'}<=set(effect):
            raise SemanticNodeError('Whole-hand discard fields are incomplete or unknown')
        if effect['op']!=self.operation or effect['actor']!=context.actor:
            raise SemanticNodeError('Whole-hand discard actor changed')
        raw=effect['players']
        if raw=='all':
            players=context.query.apnap_order
        elif raw=='opponents':
            players=tuple(p for p in context.query.apnap_order if p!=context.actor)
        elif isinstance(raw,(list,tuple)) and len(raw)==1 and type(raw[0]) is str:
            players=(context.query.require_active_seat(raw[0]),)
        else:
            raise SemanticNodeError('Whole-hand discard requires a represented player set')
        try:
            intent=DiscardWholeHandsIntent(actor=context.actor,players=players,reason=str(effect.get('reason') or context.default_reason),
                replacement_selections=effect.get('_replacement_selections',()))
        except (TypeError,ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation,handler_id=self.handler_id,intents=(intent,))


def execute_whole_hand_discard(sink,intent):
    from ..whole_hand_discard import resolve_whole_hand_discard
    from ..zone_trigger_events import ZoneTransitionKind
    def commit(refs):
        return sink.move_objects_simultaneously_intent(MoveObjectsSimultaneouslyIntent(
            actor=intent.actor,object_refs=refs,expected_zones=('hand',),destination='graveyard',reason=intent.reason,
            transition_kind=ZoneTransitionKind.DISCARD,replacement_selections=intent.replacement_selections))
    return resolve_whole_hand_discard(sink,intent,commit_batch=commit)
