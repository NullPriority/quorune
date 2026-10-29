"""Result-bound discard preparation followed by ordinary drawing."""
from dataclasses import dataclass
from ..discard_result_model import (DISCARD_RESULT_CAPABILITY, DISCARD_RESULT_OPERATION,
    DiscardWholeHandsResultIntent, IntentResultPlayerDraws, validate_discard_draw)
from ..replacement.immutable import FrozenMap
from .model import AutoContinue, SemanticChoiceError, SemanticChoicePreparation


@dataclass(frozen=True, slots=True)
class DiscardResultDrawHandler:
    operation: str = DISCARD_RESULT_OPERATION
    handler_id: str = 'choice.hand.discard-result-draw.v1'
    schema_version: int = 1
    rule_references: tuple[str, ...] = ('701.9a', '701.9c', '608.2c', '608.2e', '121.2', '121.2c')
    capability_dependencies: tuple[str, ...] = (DISCARD_RESULT_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ('actor', 'players', 'reduction', 'offset')
    private_data: tuple[str, ...] = ('DiscardWholeHandsResultIntent.hands',)
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = ('rules.discard_results.execute_discard_result', 'semantic_choices.intent_host.move_objects_simultaneously_intent')
    replay_fixture: str = 'whole-hand-discard-results'
    test_modules: tuple[str, ...] = ('tests.test_discard_results', 'tests.test_discard_result_native')

    def prepare(self, effect, context):
        try:
            validate_discard_draw(effect)
            if effect['actor'] != context.actor:
                raise ValueError('Discard-result controller changed')
            order = context.query.apnap_order
            if not order or len(order) != len(set(order)) or set(order) != set(context.query.active_seats):
                raise ValueError('Discard-result APNAP order is unavailable')
            players = tuple(order) if effect['players'] == 'all' else tuple(effect['players'])
            if any(player not in context.query.active_seats for player in players):
                raise ValueError('Discard-result player is unavailable')
            hands = context.query.discard_hand_inputs
            if hands is None:raise ValueError('Authoritative discard membership was not materialized')
            intent = DiscardWholeHandsResultIntent(context.actor, players, hands, context.stack_label)
        except (ValueError, TypeError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect), preparation_intents=(intent,),
            auto_continue=AutoContinue(reason='Whole-hand discard committed before result-bound draws',
                player_draws_from_result=IntentResultPlayerDraws(0, effect['reduction'], effect['offset'])))

    def complete(self, continuation, response, query):
        raise SemanticChoiceError('Discard-result instructions do not request a subset')
