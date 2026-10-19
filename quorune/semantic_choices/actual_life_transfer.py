from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from ..replacement.immutable import FrozenMap
from ..rules.actual_life_transfer import ACTUAL_LIFE_TRANSFER_CAPABILITY,ACTUAL_LIFE_TRANSFER_OPERATION
from ..semantic_runtime.life_batch_intent import LifeLossBatchIntent
from ..semantic_runtime.intents import LifeChangeIntent
from .model import AutoContinue,IntentResultAmount,SemanticChoicePreparation,SemanticChoiceError


@dataclass(frozen=True,slots=True)
class ActualLifeTransferHandler:
    operation: str=ACTUAL_LIFE_TRANSFER_OPERATION
    handler_id: str='choice.life.actual-opponent-loss-transfer.v1'
    schema_version: int=1
    rule_references: tuple[str,...]=('119.3','119.9','608.2c')
    capability_dependencies: tuple[str,...]=(ACTUAL_LIFE_TRANSFER_CAPABILITY,'life.change.effect')
    continuation_fields: tuple[str,...]=('op','schema_version','player','amount','stage')
    private_data: tuple[str,...]=()
    projected_fields: tuple[str,...]=()
    mutation_path: tuple[str,...]=('semantic_choices.intent_host.apply_life_loss_batch_intent',)
    replay_fixture: str='actual-opponent-life-loss-transfer'
    test_modules: tuple[str,...]=('tests.test_opening_hand_reveal',)

    def prepare(self,effect,context):
        gain_stage=isinstance(effect,Mapping) and effect.get('stage')=='gain'
        if (not isinstance(effect,Mapping) or set(effect)!=({'op','schema_version','player','amount'}|({'stage'} if gain_stage else set()))
                or effect['op']!=self.operation or type(effect['schema_version']) is not int or effect['schema_version']!=1
                or effect['player']!=context.actor or context.actor not in context.query.active_seats
                or type(effect['amount']) is not int or effect['amount']<(0 if gain_stage else 1)):
            raise SemanticChoiceError('Actual life transfer requires its fixed controller and amount')
        if gain_stage:
            intents=(LifeChangeIntent(context.actor,context.actor,effect['amount'],context.stack_label,context.source_ref),) if effect['amount'] else ()
            return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),preparation_intents=intents,
                auto_continue=AutoContinue(reason='Actual opponent life loss transferred'))
        opponents=tuple(p for p in context.query.active_seats if p!=context.actor)
        intents=(LifeLossBatchIntent(context.actor,opponents,effect['amount'],context.stack_label,context.source_ref),) if opponents else ()
        gain=FrozenMap({**dict(effect),'stage':'gain','amount':0})
        return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),preparation_intents=intents,
            auto_continue=AutoContinue(reason='Opponent life loss committed before its actual-total gain',
                prepend_effects=(gain,),amount_from_result=IntentResultAmount(0,0,'amount') if opponents else None))

    def complete(self,continuation,response,query):
        raise SemanticChoiceError('Actual life transfer never issues an external choice')
