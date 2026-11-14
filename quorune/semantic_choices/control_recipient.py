"""An untargeted opponent chosen by the resolving object's controller."""
from collections.abc import Mapping
from dataclasses import dataclass
from ..recipient_control_model import RECIPIENT_CONTROL_CAPABILITY,CHOOSE_CONTROL_RECIPIENT_OPERATION,recipient_control_effect
from ..replacement.immutable import FrozenMap
from .model import (AutoContinue,ScalarChoice,SemanticChoiceCompletion,SemanticChoiceError,
    SemanticChoicePreparation,SemanticChoiceRequest)

_FIELDS={'op','schema_version','player','card','opponent_only'}
_LOCAL={'_legal_recipients','_subject_incarnation'}
_RESPONSE={'recipient','action','action_id','a','cap','reason','plan','next','choice_schema'}


def _validate(effect):
    if (not isinstance(effect,Mapping) or set(effect)!=_FIELDS or effect['op']!=CHOOSE_CONTROL_RECIPIENT_OPERATION
            or type(effect['schema_version']) is not int or effect['schema_version'] not in {1,2}
            or effect['opponent_only'] is not True or type(effect['player']) is not str or not effect['player']
            or effect['card'] is not None and (type(effect['card']) is not str or not effect['card'])):
        raise SemanticChoiceError('Opponent control choice requires its closed versioned subject fields')


@dataclass(frozen=True,slots=True)
class ControlRecipientChoiceHandler:
    operation: str=CHOOSE_CONTROL_RECIPIENT_OPERATION
    handler_id: str='choice.control.opponent-recipient.v1'
    schema_version: int=1
    rule_references: tuple[str,...]=('CR 110.2','CR 115.1d','CR 608.2d','CR 611.2a','CR 613.1b')
    capability_dependencies: tuple[str,...]=(RECIPIENT_CONTROL_CAPABILITY,)
    continuation_fields: tuple[str,...]=tuple(sorted(_FIELDS|_LOCAL))
    private_data: tuple[str,...]=()
    projected_fields: tuple[str,...]=('prompt','legal_actions.choice_schema.legal_values')
    mutation_path: tuple[str,...]=('GiveControlIntent','control_effects.gain_control_of_refs')
    replay_fixture: str='untargeted-opponent-control-recipient'
    test_modules: tuple[str,...]=('tests.test_recipient_control',)

    def prepare(self,effect,context):
        _validate(effect)
        if effect['player']!=context.actor or context.actor!=context.stack_controller:
            raise SemanticChoiceError('Control recipient is chosen by the resolving controller')
        legal=tuple(p for p in context.query.active_seats if p!=context.actor)
        subject=context.query.object(effect['card'],zones=('battlefield',)) if effect['card'] is not None else None
        local={**dict(effect),'_legal_recipients':list(legal),'_subject_incarnation':subject.logical_object_id if subject else None}
        if subject is None or not legal:
            return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(local),
                auto_continue=AutoContinue(reason='No original permanent or opponent remains'))
        return SemanticChoicePreparation(request=SemanticChoiceRequest(prompt='Choose an opponent to gain control.',
            choice=ScalarChoice(field_name='recipient',legal_values=legal),
            public_context=FrozenMap({'stack':context.stack_ref,'operation':self.operation})),continuation_effect=FrozenMap(local))

    def complete(self,continuation,response,query):
        effect=continuation.effect
        if set(effect)!=_FIELDS|_LOCAL:raise SemanticChoiceError('Control recipient continuation fields changed')
        _validate({k:v for k,v in effect.items() if k in _FIELDS})
        if effect['player']!=continuation.semantic_frame.controller:
            raise SemanticChoiceError('Control recipient chooser changed')
        legal=effect['_legal_recipients'];actor=effect['player']
        if (not isinstance(legal,(list,tuple)) or any(type(p) is not str or p not in query.seats or p==actor for p in legal)
                or len(set(legal))!=len(legal) or set(response)-_RESPONSE):
            raise SemanticChoiceError('Control recipient participant identities changed')
        if effect['_subject_incarnation'] is None or not legal:return SemanticChoiceCompletion()
        chosen=response.get('recipient')
        if type(chosen) is not str or chosen not in legal or chosen not in query.active_seats:
            raise SemanticChoiceError('Choose a current opponent from the offered recipients')
        subject=query.object(effect['card'],zones=('battlefield',))
        if subject is None or subject.logical_object_id!=effect['_subject_incarnation']:
            raise SemanticChoiceError('Control recipient subject incarnation changed')
        return SemanticChoiceCompletion(prepend_effects=(FrozenMap(recipient_control_effect(card=effect['card'],controller=chosen)),))
