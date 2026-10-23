from __future__ import annotations

from dataclasses import dataclass
from ..created_token_counter_model import CREATE_THEN_COUNTER,COUNTER_CREATED,DEPENDENCIES,validate_created_token_counter
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import CreateTokenIntent,PlaceCountersIntent
from .model import (AutoContinue,IntentResultObjects,SemanticChoicePreparation,SemanticChoiceError)


@dataclass(frozen=True,slots=True)
class CreatedTokenCounterHandler:
    operation: str
    handler_id: str
    schema_version: int = 1
    rule_references: tuple[str,...] = ('111.3','122.1','608.2c','704.4')
    capability_dependencies: tuple[str,...] = DEPENDENCIES
    continuation_fields: tuple[str,...] = ('controller','source','token','token_mechanics','cards','amount')
    private_data: tuple[str,...] = ()
    projected_fields: tuple[str,...] = ()
    mutation_path: tuple[str,...] = ('CreateTokenIntent','PlaceCountersIntent')
    replay_fixture: str = 'created-token-counter-followup'
    test_modules: tuple[str,...] = ('tests.test_created_token_counters',)

    def prepare(self,effect,context):
        try:validate_created_token_counter(effect,resolved=self.operation==COUNTER_CREATED)
        except (ValueError,TypeError,KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc
        actor=context.actor
        if effect['controller']!=actor or actor not in context.query.active_seats or effect['source']!=context.source_ref:
            raise SemanticChoiceError('Created-token counter parent, controller or source changed')
        if self.operation==CREATE_THEN_COUNTER:
            token=effect['token']
            intent=CreateTokenIntent(actor=actor,controller=actor,name=token['name'],quantity=1,
                characteristics=FrozenMap(token['characteristics']),tapped=token.get('tapped',False),reason=context.stack_label)
            follow=FrozenMap({'op':COUNTER_CREATED,'schema_version':1,'controller':actor,
                'source':effect['source'],'amount':effect['amount'],'cards':[]})
            return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),preparation_intents=(intent,),
                auto_continue=AutoContinue(reason='Apply the following instruction to actual creation results.',
                    prepend_effects=(follow,),objects_from_result=IntentResultObjects(0,0)))
        refs=tuple(ref for ref in effect['cards'] if context.query.object(ref,zones=('battlefield',)) is not None)
        intents=(PlaceCountersIntent(actor=actor,object_refs=refs,counter_name='+1/+1',amount=effect['amount'],
            reason=context.stack_label,source_ref=effect['source']),) if refs and effect['amount'] else ()
        return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),preparation_intents=intents,
            auto_continue=AutoContinue(reason='Created-object counter instruction completed.'))

    def complete(self,continuation,response,query):
        raise SemanticChoiceError('Created-token counter instructions do not accept a player-selected recipient')


CREATED_TOKEN_COUNTER_HANDLERS=(CreatedTokenCounterHandler(CREATE_THEN_COUNTER,'choice.token.create-then-counters.v1'),
    CreatedTokenCounterHandler(COUNTER_CREATED,'choice.token.counter-created-results.v1'))
