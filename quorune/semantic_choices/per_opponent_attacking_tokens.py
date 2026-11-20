"""Choose within each original opponent group, then create one canonical batch."""
from collections.abc import Mapping

from ..attacking_opponent_model import AttackingOpponentScope, OpponentAttackDestination
from ..replacement.immutable import FrozenMap
from ..rules.per_opponent_attacking_tokens import per_opponent_token_inputs
from ..semantic_runtime.intents import CreateTokenIntent
from .model import (
    AutoContinue, DecisionMapChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)

_PLAN_FIELDS = frozenset({
    '_choice_actor', '_opponents', '_token_ids', '_opponent_by_token', '_preview_specs',
    '_assignments', '_current_opponent', '_pending_token_ids', '_destinations', '_stack_label',
    '_replacement_selections',
})


def _public(effect, actor):
    if (any(key.startswith('_') and key not in _PLAN_FIELDS for key in effect)
            or effect.get('_choice_actor', actor) != actor
            or not isinstance(effect.get('_replacement_selections', ()), (tuple, list))):
        raise SemanticChoiceError('Opponent token continuation has unknown fields or principal')
    return {key: value for key, value in effect.items()
        if not key.startswith('_') or key == '_replacement_selections'}


def _destinations(query, scope, opponent):
    result = [opponent]
    if (opponent in query.active_seats
            and scope.destination is OpponentAttackDestination.PLAYER_OR_PLANESWALKER):
        result.extend(sorted(row.ref for row in query.objects(zones=('battlefield',))
            if row.controller == opponent and 'planeswalker' in row.types
            and not row.phased_out))
    return tuple(result)



def _preview_rows(value):
    if not isinstance(value, (tuple, list)) or any(not isinstance(row, Mapping) for row in value):
        raise SemanticChoiceError('Opponent token preview is not a sequence of fixed definitions')
    return tuple(row if isinstance(row, FrozenMap) else FrozenMap(row) for row in value)


def _plan(effect, actor, query):
    opponents, groups, common = per_opponent_token_inputs(actor, effect,
        seats=query.seats, active_seats=query.active_seats,
        attacking=query.attacking_token_entry_allowed)
    preview = _preview_rows(query.token_creation_preview())
    expanded = []
    for row in preview:
        group, quantity = row.get('_attacking_group'), row.get('quantity', 1)
        if group is None:
            continue
        if group not in opponents or type(quantity) is not int or quantity < 0:
            raise SemanticChoiceError('Opponent token replacement preview changed its original group')
        expanded.extend([group] * quantity)
    ordinals = {}; tokens = []
    for group in expanded:
        ordinal = ordinals.get(group, 0); ordinals[group] = ordinal + 1
        tokens.append(f'opponent-token:{group}:{ordinal}')
    return opponents, groups, common, preview, tuple(expanded), tuple(tokens)


def _intent(effect, actor, common, *, assignments=()):
    return CreateTokenIntent(actor=actor, controller=actor,
        name=common['name'], quantity=common['quantity'], tapped=True,
        characteristics=FrozenMap(common['characteristics']),
        attacking_groups=tuple(common['attacking_groups']),
        attacking_assignments=tuple(assignments),
        reason=str(effect.get('_stack_label') or 'Per-opponent token creation'),
        replacement_selections=tuple(effect.get('_replacement_selections', ())))


def _automatic(effect, intents=(), reason='The specified token destinations are fixed.'):
    return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
        preparation_intents=tuple(intents), auto_continue=AutoContinue(reason=reason))


def prepare_per_opponent_tokens(effect, context):
    actor, query = context.actor, context.query
    try:
        if actor != context.stack_controller:
            raise SemanticChoiceError('Opponent tokens require their current ability controller')
        public = _public(effect, actor)
        opponents, groups, common, preview, expanded, tokens = _plan(effect, actor, query)
        if not groups:
            return _automatic(public, reason='There are no opponent tokens to create.')
        if not query.attacking_token_entry_allowed:
            return _automatic(public, (_intent(public, actor, common),),
                reason='The tokens enter under a player who is not attacking.')
        scope = AttackingOpponentScope.from_dict(effect['opponent_scope'])
        assignments = dict(effect.get('_assignments', {})) if preview == _preview_rows(effect.get('_preview_specs', preview)) else {}
        if set(assignments) - set(tokens):
            raise SemanticChoiceError('Opponent token assignments exceed the original plan')
        for token, opponent in zip(tokens, expanded):
            legal = _destinations(query, scope, opponent)
            if assignments.get(token) not in legal:
                assignments.pop(token, None)
            if len(legal) == 1:
                assignments[token] = legal[0]
        if len(assignments) == len(tokens):
            if not tokens:
                return _automatic(public, reason='No replacement-created token needs an attack destination.')
            return _automatic(public, (_intent(public, actor, common,
                assignments=tuple(assignments[token] for token in tokens)),))
        current = next(group for token, group in zip(tokens, expanded) if token not in assignments)
        pending = tuple(token for token, group in zip(tokens, expanded) if group == current and token not in assignments)
        legal = _destinations(query, scope, current)
        saved = {**public, '_choice_actor': actor, '_opponents': opponents,
            '_token_ids': tokens, '_opponent_by_token': expanded, '_preview_specs': preview,
            '_assignments': assignments, '_current_opponent': current,
            '_pending_token_ids': pending, '_destinations': legal, '_stack_label': context.stack_label}
        return SemanticChoicePreparation(request=SemanticChoiceRequest(
            prompt=f'Choose what the tokens assigned to {current} attack.',
            choice=DecisionMapChoice('attacking', pending, len(pending), legal),
            public_context=FrozenMap({'stack': context.stack_ref, 'operation': effect['op'],
                'destinations': legal})), continuation_effect=FrozenMap(saved))
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def complete_per_opponent_tokens(continuation, response, query):
    effect = continuation.effect; actor = effect.get('_choice_actor')
    try:
        _public(effect, actor)
        if set(response) - {'action', 'action_id', 'choice_schema', 'attacking'}:
            raise SemanticChoiceError('Opponent token choices contain unknown response fields')
        opponents, groups, common, preview, expanded, tokens = _plan(effect, actor, query)
        if (tuple(effect.get('_token_ids', ())) != tokens
                or tuple(effect.get('_opponent_by_token', ())) != expanded
                or preview != _preview_rows(effect.get('_preview_specs', ()))):
            # No creation has committed yet. Re-present the canonical changed plan.
            public = _public(effect, actor)
            return SemanticChoiceCompletion(repeat_effect=FrozenMap({**public, '_opponents': opponents}))
        scope = AttackingOpponentScope.from_dict(effect['opponent_scope'])
        current = effect.get('_current_opponent')
        pending = tuple(effect.get('_pending_token_ids', ()))
        raw = response.get('attacking')
        legal = _destinations(query, scope, current)
        if (not query.attacking_token_entry_allowed or current not in opponents
                or not pending or any(token not in tokens or expanded[tokens.index(token)] != current for token in pending)
                or not isinstance(raw, Mapping) or set(raw) != set(pending)
                or any(type(value) is not str or value not in legal for value in raw.values())):
            raise SemanticChoiceError('Opponent token assignments are incomplete or outside their bound player')
        assignments = dict(effect.get('_assignments', {})); assignments.update(raw)
        return SemanticChoiceCompletion(repeat_effect=FrozenMap({
            **dict(effect), '_assignments': assignments}))
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
