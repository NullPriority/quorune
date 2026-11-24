"""Result-bound discard preparation followed by ordinary drawing."""
from dataclasses import dataclass
from ..discard_result_model import (DISCARD_GRAVEYARD_ORDER_FIELD, DISCARD_RESULT_CAPABILITY, DISCARD_RESULT_OPERATION,
    EVENT_PLAYER_DISCARD_RESULT_VERSION, DiscardWholeHandsResultIntent, IntentResultPlayerDraws, ReducedDiscardResultDraws,
    SELECTED_LAND_DISCARD_RESULT_VERSION, SELECTED_LAND_DISCARD_FIELD, SelectedLandDiscardCounterDraws,
    event_discard_player_departed, validate_discard_draw)
from ..replacement.immutable import FrozenMap
from .model import AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest


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
            if event_discard_player_departed(effect, seats=context.query.seats, active_seats=context.query.active_seats):
                return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                    auto_continue=AutoContinue(reason='The affected player is no longer in the game'))
            order = context.query.apnap_order
            if not order or len(order) != len(set(order)) or set(order) != set(context.query.active_seats):
                raise ValueError('Discard-result APNAP order is unavailable')
            players = tuple(order) if effect['players'] == 'all' else tuple(effect['players'])
            if any(player not in context.query.active_seats for player in players):
                raise ValueError('Discard-result player is unavailable')
            selected_lands = effect['schema_version'] == SELECTED_LAND_DISCARD_RESULT_VERSION
            if selected_lands and SELECTED_LAND_DISCARD_FIELD not in effect:
                return self._prepare_land_selection(effect, context)
            hands = context.query.discard_hand_inputs
            if hands is None:raise ValueError('Authoritative discard membership was not materialized')
            intent = DiscardWholeHandsResultIntent(
                context.actor, players, hands, context.stack_label,
                require_graveyard_order=selected_lands or effect["schema_version"] == EVENT_PLAYER_DISCARD_RESULT_VERSION,
                selected_lands=selected_lands,
                discard_effect_origin=getattr(context.query, "discard_effect_origin", None),
            )
        except (ValueError, TypeError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        binding = (SelectedLandDiscardCounterDraws if selected_lands
                   else ReducedDiscardResultDraws if effect['schema_version'] == 2 else IntentResultPlayerDraws)
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect), preparation_intents=(intent,),
            auto_continue=AutoContinue(reason='Selected lands committed before counters and draws' if selected_lands
                else 'Whole-hand discard committed before result-bound draws',
                player_draws_from_result=binding(0, effect['reduction'], effect['offset'])))

    def _prepare_land_selection(self, effect, context):
        from .library_and_hand import _private_cards

        rows = tuple(sorted(
            (row for row in context.query.objects(zones=('hand',), owner=context.actor) if 'land' in row.types),
            key=lambda row: row.ref,
        ))
        if not rows:
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason='No legal land card can be discarded'))
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt='Choose any number of land cards to discard.',
                choice=ObjectChoice('cards', tuple(row.ref for row in rows), zones=('hand',),
                    minimum=0, maximum=len(rows), optional=True, visibility='actor_private', owner_relation='actor'),
                public_context=FrozenMap({'operation': self.operation, 'stack': context.stack_ref,
                    'cards': _private_cards(rows)}),
            ),
            continuation_effect=FrozenMap(effect),
        )

    def _complete_land_selection(self, continuation, response, query):
        effect = continuation.effect
        actor = continuation.semantic_frame.controller
        selected = response.get('cards', ())
        if (effect['actor'] != actor or not isinstance(selected, (list, tuple))
                or any(type(ref) is not str or not ref for ref in selected) or len(set(selected)) != len(selected)):
            raise SemanticChoiceError('The private land selection is malformed')
        if not selected:
            return SemanticChoiceCompletion()
        eligible = {row.ref for row in query.objects(zones=('hand',), owner=actor) if 'land' in row.types}
        snapshot = query.discard_hand_inputs
        members = {} if snapshot is None else {member['ref']: member for member in snapshot[actor]}
        if any(ref not in eligible or ref not in members for ref in selected):
            raise SemanticChoiceError('A selected card is outside the current retained land choice')
        ordered = tuple(sorted((members[ref] for ref in selected), key=lambda row: row['object_id']))
        return SemanticChoiceCompletion(repeat_effect=FrozenMap({**effect, SELECTED_LAND_DISCARD_FIELD: ordered}))


    def complete(self, continuation, response, query):
        if DISCARD_GRAVEYARD_ORDER_FIELD in continuation.effect:
            from .discard_graveyard_order import complete_discard_graveyard_order

            return complete_discard_graveyard_order(continuation, response, query)
        if (continuation.effect.get('schema_version') == SELECTED_LAND_DISCARD_RESULT_VERSION
                and SELECTED_LAND_DISCARD_FIELD not in continuation.effect):
            return self._complete_land_selection(continuation, response, query)
        raise SemanticChoiceError('Discard-result instructions do not request a subset')
