"""Ordinary fixed creature definitions with individually chosen combat entry."""
from dataclasses import dataclass
from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from ..rules.attacking_token_results import (ATTACKING_TOKEN_OPERATION, ATTACKING_TOKEN_CAPABILITY,
    attacking_token_result_closure)
from ..semantic_runtime.intents import CreateTokenIntent
from .model import AutoContinue, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation
from .token_destination_plans import prepare_attacking_destinations, complete_attacking_destinations


def _validate_effect(effect, actor):
    public = {key: value for key, value in effect.items() if key != '_replacement_selections'}
    if public.get('player') != actor or not isinstance(effect.get('_replacement_selections', ()), (list, tuple)):
        raise SemanticChoiceError('Fixed attacking token principal or replacements are malformed')
    public['player'] = '$controller'
    try:
        attacking_token_result_closure(public)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class FixedAttackingTokenDestinationHandler:
    operation: str = ATTACKING_TOKEN_OPERATION
    handler_id: str = 'choice.token.fixed-attacking-destinations.v1'
    schema_version: int = 1
    rule_references: tuple[str, ...] = ('CR 111.2', 'CR 506.3b', 'CR 508.4', 'CR 614.16')
    capability_dependencies: tuple[str, ...] = (ATTACKING_TOKEN_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ('player', 'quantity', 'name', 'characteristics',
        'tapped', 'token_entry_version', '_choice_actor', '_token_ids', '_destinations',
        '_stack_label', '_preview_specs', '_outside_combat')
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ('prompt', 'destinations',
        'legal_actions.choice_schema.legal_refs', 'legal_actions.choice_schema.legal_values')
    mutation_path: tuple[str, ...] = ('CreateTokenIntent through replacement-aware token creation',)
    replay_fixture: str = 'ordinary-fixed-attacking-token-results'
    test_modules: tuple[str, ...] = ('tests.test_attacking_token_results',)

    def prepare(self, effect, context):
        _validate_effect(effect, context.actor)
        if not context.query.attacking_token_entry_allowed:
            return SemanticChoicePreparation(request=None,
                continuation_effect=FrozenMap({**dict(effect), '_choice_actor': context.actor,
                    '_outside_combat': True, '_stack_label': context.stack_label}),
                auto_continue=AutoContinue(reason='controller is not an attacking player'))
        return prepare_attacking_destinations(effect, context, operation=self.operation,
            label='entering', prefix='attacking')

    def complete(self, continuation, response, query):
        effect = continuation.effect
        actor = effect.get('_choice_actor')
        if (set(effect) - {'op', '_replacement_selections'} - set(self.continuation_fields)
                or actor not in query.active_seats):
            raise SemanticChoiceError('Fixed attacking token continuation has unknown fields or principal')
        base = {key: value for key, value in effect.items() if not key.startswith('_')}
        _validate_effect(base, actor)
        if effect.get('_outside_combat') is True:
            if query.attacking_token_entry_allowed or not isinstance(response, Mapping) or response:
                raise SemanticChoiceError('Noncombat token continuation is stale or malformed')
            return SemanticChoiceCompletion(intents=(CreateTokenIntent(actor=actor, controller=actor,
                name=effect['name'], quantity=effect['quantity'], tapped=effect['tapped'],
                characteristics=FrozenMap(effect['characteristics']),
                reason=str(effect.get('_stack_label') or 'Token creation'),
                replacement_selections=tuple(effect.get('_replacement_selections', ()))),))
        if not query.attacking_token_entry_allowed:
            raise SemanticChoiceError('Attacking token continuation is outside combat')
        return complete_attacking_destinations(effect, response, query, name=effect['name'],
            tapped=effect['tapped'], sacrifice=False, label='Entering')
