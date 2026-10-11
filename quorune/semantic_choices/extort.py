from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import LifeChangeIntent,PayManaCostIntent
from ..semantic_runtime.life_batch_intent import LifeLossBatchIntent
from ..rules.extort import EXTORT_OPERATION,EXTORT_CAPABILITY
from .model import AutoContinue,IntentResultAmount,ScalarChoice,SemanticChoiceCompletion,SemanticChoiceError,SemanticChoicePreparation,SemanticChoiceRequest


def _mana(color):
    return FrozenMap({key:int(key==color) for key in ('GENERIC','W','U','B','R','G','C')})


def _validate(effect,actor,query):
    if not isinstance(effect,Mapping) or effect.get('op')!=EXTORT_OPERATION or type(effect.get('schema_version')) is not int or effect['schema_version']!=1:
        raise SemanticChoiceError('Extort instruction identity is malformed')
    if effect.get('player')!=actor or actor not in query.active_seats or type(effect.get('source')) is not str or not effect['source']:
        raise SemanticChoiceError('Extort controller or source identity is malformed')
    stage=effect.get('stage');base={'op','schema_version','player','source','stage'}
    allowed=base|{'amount'} if stage=='gain' else base
    if stage not in {'pay','loss','gain'} or set(effect)!=allowed:
        raise SemanticChoiceError('Extort stage fields are malformed')
    amount=effect.get('amount')
    if stage=='gain' and (type(amount) is not int or amount<0):raise SemanticChoiceError('Extort gain requires actual resolved loss')
    return stage,amount


@dataclass(frozen=True,slots=True)
class ExtortChoiceHandler:
    operation: str = EXTORT_OPERATION
    handler_id: str = 'choice.payment.extort.v1'
    schema_version: int = 1
    rule_references: tuple[str,...] = ('702.101a','119.3','119.9','608.2c')
    capability_dependencies: tuple[str,...] = (EXTORT_CAPABILITY,)
    continuation_fields: tuple[str,...] = ('player','source','stage','amount')
    private_data: tuple[str,...] = ()
    projected_fields: tuple[str,...] = ('legal_actions.choice_schema.legal_values','stack','operation')
    mutation_path: tuple[str,...] = ('semantic_choices.intent_host.pay_mana_cost_intent','semantic_choices.intent_host.apply_life_loss_batch_intent','semantic_choices.intent_host.apply_life_change_intent')
    replay_fixture: str = 'extort-ordered-life-transfer'
    test_modules: tuple[str,...] = ('tests.test_extort_rules',)

    def prepare(self,effect,context):
        actor=context.actor;stage,amount=_validate(effect,actor,context.query)
        if stage=='pay':
            values=('decline',*(color for color in ('W','B') if context.query.cost_is_affordable(actor,_mana(color))))
            return SemanticChoicePreparation(request=SemanticChoiceRequest(prompt='Pay one white or black mana for Extort, or decline.',
                choice=ScalarChoice(field_name='pay',legal_values=values),public_context=FrozenMap({'stack':context.stack_ref,'operation':self.operation})),continuation_effect=FrozenMap(effect))
        if stage=='loss':
            opponents=tuple(p for p in context.query.active_seats if p!=actor)
            intents=(LifeLossBatchIntent(actor,opponents,1,context.stack_label,effect['source']),) if opponents else ()
            return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),preparation_intents=intents,
                auto_continue=AutoContinue(reason='Extort opponent loss committed',prepend_effects=(FrozenMap({**dict(effect),'stage':'gain','amount':0}),),
                    amount_from_result=IntentResultAmount(0,0,'amount') if opponents else None))
        intents=(LifeChangeIntent(actor,actor,amount,context.stack_label,effect['source']),) if amount else ()
        return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),preparation_intents=intents,
            auto_continue=AutoContinue(reason='Extort actual lost life transferred'))

    def complete(self,continuation,response,query):
        effect=continuation.effect;actor=continuation.semantic_frame.controller
        stage,_=_validate(effect,actor,query)
        if stage!='pay':raise SemanticChoiceError('Only Extort payment has an external choice')
        allowed={'pay','action','action_id','a','cap','reason','plan','next','choice_schema'}
        if set(response)-allowed:raise SemanticChoiceError('Extort payment response has unknown fields')
        color=response.get('pay','decline')
        if color=='decline':return SemanticChoiceCompletion()
        if type(color) is not str or color not in {'W','B'} or not query.cost_is_affordable(actor,_mana(color)):
            raise SemanticChoiceError('Extort requires one payable white or black mana')
        intent=PayManaCostIntent(actor,actor,_mana(color),'Extort payment','effect.extort.paid','Extort cost paid.',FrozenMap({'color':color}))
        return SemanticChoiceCompletion(intents=(intent,),prepend_effects=(FrozenMap({**dict(effect),'stage':'loss'}),))
