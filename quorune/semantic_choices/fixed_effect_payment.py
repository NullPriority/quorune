from __future__ import annotations

"""Version-2 payload support for the existing registered payment choice."""

from typing import Any, Mapping

from ..fixed_effect_payment import FixedEffectPaymentSpec
from ..object_query import object_matches_query
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent, PayLifeIntent, PayManaCostIntent
from ..zone_trigger_events import ZoneTransitionKind
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    ObjectChoice, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceContinuation,
    SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest,
)
from .optional_effect import _represented_effect


def _validated(
    effect: Mapping[str, Any], actor: str, query: SemanticChoiceQuery,
    *, continuation: bool = False,
) -> tuple[FixedEffectPaymentSpec, tuple[FrozenMap, ...]]:
    fields = {'op', 'schema_version', 'player', 'payment', 'effects'}
    local = {'_choice_actor', '_legal_objects', '_stack_label'} if continuation else set()
    if (
        set(effect) not in (fields | local, fields | local | {'cost'})
        or effect.get('op') != 'offer_optional_mana_payment'
        or type(effect.get('schema_version')) is not int
        or effect['schema_version'] != 2
    ):
        raise SemanticChoiceError('Fixed effect payment fields are malformed')
    if effect['player'] != actor or actor not in query.active_seats:
        raise SemanticChoiceError('Fixed payment controller changed')
    try:
        spec = FixedEffectPaymentSpec.from_dict(effect['payment'])
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if ('cost' in effect) != (spec.kind == 'mana') or spec.kind == 'mana' and dict(effect['cost']) != dict(spec.requirements):
        raise SemanticChoiceError('Fixed payment mana identity changed')
    if spec.kind == 'discard' and spec.predicate.owner != actor or spec.kind == 'sacrifice' and spec.predicate.controller != actor:
        raise SemanticChoiceError('Fixed payment object relation changed')
    effects = effect['effects']
    if not isinstance(effects, (tuple, list)) or not 1 <= len(effects) <= 4:
        raise SemanticChoiceError('Fixed payment needs closed typed consequences')
    for nested in effects:
        if not isinstance(nested, Mapping) or nested.get('op') in {'offer_optional_effect', 'offer_optional_mana_payment'}:
            raise SemanticChoiceError('Fixed payments cannot nest')
        _represented_effect(nested, actor=actor, query=query)
    return spec, tuple(FrozenMap(nested) for nested in effects)


def prepare_fixed_effect_payment(
    effect: Mapping[str, Any], context: SemanticChoiceContext,
) -> SemanticChoicePreparation:
    actor = context.actor
    spec, _ = _validated(effect, actor, context.query)
    return prepare_fixed_payment_cost(spec, effect, context)


def prepare_fixed_payment_cost(
    spec: FixedEffectPaymentSpec, effect: Mapping[str, Any],
    context: SemanticChoiceContext,
) -> SemanticChoicePreparation:
    """Publish the existing closed cost independently of its result branch."""
    actor = context.actor
    legal = ()
    if spec.kind in {'discard', 'sacrifice'}:
        candidates = context.query.objects(
            zones=spec.predicate.zones,
            owner=actor if spec.kind == 'discard' else None,
            controller=actor if spec.kind == 'sacrifice' else None,
        )
        legal = tuple(sorted(
            (row for row in candidates if object_matches_query(row, spec.predicate)),
            key=lambda row: (row.ref, row.logical_object_id),
        ))
        payable = len(legal) >= spec.amount
        choice = ObjectChoice(
            field_name='cards', legal_refs=tuple(row.ref for row in legal),
            zones=spec.predicate.zones, minimum=0,
            maximum=spec.amount if payable else 0, optional=True,
            visibility='actor_private' if spec.kind == 'discard' else 'public',
            allowed_cardinalities=((0, spec.amount) if payable else (0,)) if spec.amount > 1 else None,
            owner_relation='actor' if spec.kind == 'discard' else 'any',
            controller_relation='actor' if spec.kind == 'sacrifice' else 'any',
        )
    else:
        payable = (
            context.query.player_life(actor) >= spec.amount if spec.kind == 'life'
            else context.query.cost_is_affordable(actor, spec.requirements)
        )
        choice = ScalarChoice(field_name='pay', legal_values=(True, False) if payable else (False,))
    stored = {
        **dict(effect), '_choice_actor': actor, '_stack_label': context.stack_label,
        '_legal_objects': [{'ref': row.ref, 'logical_object_id': row.logical_object_id} for row in legal],
    }
    public = {'stack': context.stack_ref, 'operation': effect['op'], 'payment_kind': spec.kind, 'payable': payable}
    if spec.kind == 'discard':
        public['objects'] = [{'id': row.ref, 'name': row.printed_name} for row in legal]
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt='Pay the optional fixed cost or decline.', choice=choice,
            public_context=FrozenMap(public),
        ),
        continuation_effect=FrozenMap(stored),
    )


def complete_fixed_effect_payment(
    continuation: SemanticChoiceContinuation, response: Mapping[str, Any],
    query: SemanticChoiceQuery,
) -> SemanticChoiceCompletion:
    effect = continuation.effect
    actor = effect.get('_choice_actor')
    spec, consequences = _validated(effect, actor, query, continuation=True)
    completion = complete_fixed_payment_cost(spec, effect, response, query)
    return SemanticChoiceCompletion(
        intents=completion.intents,
        prepend_effects=consequences if completion.intents else (),
    )


def complete_fixed_payment_cost(
    spec: FixedEffectPaymentSpec, effect: Mapping[str, Any],
    response: Mapping[str, Any], query: SemanticChoiceQuery,
) -> SemanticChoiceCompletion:
    """Validate and commit one fixed cost through the canonical intent owner."""
    actor = effect['_choice_actor']
    label = effect['_stack_label']
    if spec.kind in {'discard', 'sacrifice'}:
        unknown = set(response) - {'cards', 'pay', 'action', 'action_id', 'a', 'cap', 'reason', 'plan', 'next', 'choice_schema'}
        if unknown:
            raise SemanticChoiceError('Fixed object payment response has unknown fields: ' + ','.join(sorted(unknown)))
        raw = response.get('cards', ())
        if (
            not isinstance(raw, (list, tuple)) or len(raw) not in {0, spec.amount}
            or any(type(ref) is not str for ref in raw) or len(set(raw)) != len(raw)
        ):
            raise SemanticChoiceError('Fixed object payment selection is malformed')
        selected = tuple(raw)
        if 'pay' in response and type(response['pay']) is not bool:
            raise SemanticChoiceError('Fixed payment flag must be boolean')
        if not selected:
            if response.get('pay', False):
                raise SemanticChoiceError('Fixed payment requires its complete selected cost')
            return SemanticChoiceCompletion()
        if response.get('pay', True) is not True:
            raise SemanticChoiceError('Selected payment conflicts with decline')
        legal = {row['ref']: row['logical_object_id'] for row in effect['_legal_objects']}
        for ref in selected:
            current = query.object(ref, zones=spec.predicate.zones)
            if ref not in legal or current is None or current.logical_object_id != legal[ref] or not object_matches_query(current, spec.predicate):
                raise SemanticChoiceError('Fixed payment object is stale or ineligible')
        intent = MoveObjectsSimultaneouslyIntent(
            actor=actor, object_refs=selected, expected_zones=spec.predicate.zones,
            destination='graveyard', reason=label,
            transition_kind=ZoneTransitionKind.DISCARD if spec.kind == 'discard' else ZoneTransitionKind.SACRIFICE,
            owned_only=spec.kind == 'discard', controlled_only=spec.kind == 'sacrifice',
        )
    else:
        unknown = set(response) - {'pay', 'action', 'action_id', 'a', 'cap', 'reason', 'plan', 'next', 'choice_schema'}
        if unknown:
            raise SemanticChoiceError('Fixed resource payment response has unknown fields: ' + ','.join(sorted(unknown)))
        if type(response.get('pay', False)) is not bool:
            raise SemanticChoiceError('Fixed resource payment choice must be boolean')
        if not response.get('pay', False):
            return SemanticChoiceCompletion()
        if spec.kind == 'life':
            if query.player_life(actor) < spec.amount:
                raise SemanticChoiceError('Fixed life payment is no longer payable')
            intent = PayLifeIntent(actor=actor, player=actor, amount=spec.amount, reason=label)
        else:
            if not query.cost_is_affordable(actor, spec.requirements):
                raise SemanticChoiceError('Fixed mana payment is no longer payable')
            intent = PayManaCostIntent(
                actor=actor, player=actor, requirements=spec.requirements, reason=label,
                event_code='effect.optional_mana.paid', message='Optional fixed effect cost paid.',
                details=FrozenMap({'cost': dict(spec.requirements)}),
            )
    return SemanticChoiceCompletion(intents=(intent,))
