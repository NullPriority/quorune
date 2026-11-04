from __future__ import annotations

from dataclasses import dataclass

from ..incubate_model import (INCUBATE_OPERATION,INCUBATE_NEXT_OPERATION,INCUBATE_DEPENDENCIES,
    IncubateIntent,validate_incubate_effect)
from ..replacement.immutable import FrozenMap
from .model import AutoContinue,SemanticChoicePreparation,SemanticChoiceError


@dataclass(frozen=True,slots=True)
class IncubateHandler:
    operation: str
    handler_id: str
    schema_version: int = 1
    rule_references: tuple[str,...] = ('107.1b','111.10i','701.53a','701.53b','614.12','707.8a')
    capability_dependencies: tuple[str,...] = INCUBATE_DEPENDENCIES
    continuation_fields: tuple[str,...] = ('schema_version','controller','source','amount','repetitions','remaining')
    private_data: tuple[str,...] = ()
    projected_fields: tuple[str,...] = ()
    mutation_path: tuple[str,...] = ('IncubateIntent','quorune.incubate_execution.execute_incubate_intent')
    replay_fixture: str = 'incubate-entry-transform'
    test_modules: tuple[str,...] = ('tests.test_incubate_rules',)

    def prepare(self,effect,context):
        try:validate_incubate_effect(effect,resolved=True)
        except (TypeError,ValueError,KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc
        if (effect['op'] != self.operation or effect['controller'] != context.actor
                or context.actor not in context.query.active_seats or effect['source'] != context.source_ref):
            raise SemanticChoiceError('Incubate parent, controller or source changed')
        repetitions = effect.get('repetitions',effect.get('remaining'))
        follow = (FrozenMap({'op':INCUBATE_NEXT_OPERATION,'schema_version':1,
            'controller':context.actor,'source':context.source_ref,'amount':effect['amount'],
            'remaining':1}),) if repetitions == 2 else ()
        return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),
            preparation_intents=(IncubateIntent(context.actor,context.actor,effect['amount'],
                context.source_ref,context.stack_label),),
            auto_continue=AutoContinue(reason='The current incubation completed.',prepend_effects=follow))

    def complete(self,continuation,response,query):
        raise SemanticChoiceError('Incubate does not accept player-selected tokens or counter amounts')


INCUBATE_HANDLERS = (IncubateHandler(INCUBATE_OPERATION,'choice.incubate.v1'),
    IncubateHandler(INCUBATE_NEXT_OPERATION,'choice.incubate-next.v1'))
