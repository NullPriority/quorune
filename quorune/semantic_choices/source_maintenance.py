from __future__ import annotations

"""Source-incarnation sacrifice around the shared resolution-time cost owner."""

from typing import Any, Mapping

from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
from ..source_maintenance import SourceMaintenanceSpec
from ..zone_trigger_events import ZoneTransitionKind
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .fixed_effect_payment import complete_fixed_payment_cost, prepare_fixed_payment_cost
from .model import (
    AutoContinue, SemanticChoiceCompletion, SemanticChoiceContinuation,
    SemanticChoiceError, SemanticChoicePreparation,
)


def _validated(
    effect: Mapping[str, Any], actor: str, query: SemanticChoiceQuery,
    *, continuation: bool = False,
) -> SourceMaintenanceSpec:
    local = {'_choice_actor', '_stack_label', '_source_logical_object_id', '_legal_objects'}
    raw = {key: value for key, value in effect.items() if key not in local}
    if set(effect) != set(raw) | (local if continuation else set()):
        raise SemanticChoiceError('Source maintenance continuation fields are malformed')
    try:
        spec = SourceMaintenanceSpec.from_effect(raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if raw['player'] != actor or actor not in query.active_seats:
        raise SemanticChoiceError('Source maintenance payer changed')
    if continuation:
        if any(type(effect[key]) is not str or not effect[key] for key in (
            '_choice_actor', '_stack_label', '_source_logical_object_id'
        )):
            raise SemanticChoiceError('Source maintenance continuation identity is malformed')
        legal = effect['_legal_objects']
        if not isinstance(legal, (tuple, list)) or any(
            not isinstance(row, Mapping) or set(row) != {'ref', 'logical_object_id'}
            or any(type(row[key]) is not str or not row[key] for key in row)
            for row in legal
        ) or len({row['ref'] for row in legal}) != len(legal):
            raise SemanticChoiceError('Source maintenance payment candidates are malformed')
    payment = spec.payment
    if payment is not None and (
        payment.kind == 'discard' and payment.predicate.owner != actor
        or payment.kind == 'sacrifice' and payment.predicate.controller != actor
    ):
        raise SemanticChoiceError('Source maintenance payment relation changed')
    return spec


def _declined_source_sacrifice(
    effect: Mapping[str, Any], query: SemanticChoiceQuery,
) -> SemanticChoiceCompletion:
    actor = effect['_choice_actor']
    source = query.object(effect['source'], zones=('battlefield',)) if effect['source'] else None
    if (
        source is None or source.phased_out or source.controller != actor
        or source.logical_object_id != effect['_source_logical_object_id']
    ):
        return SemanticChoiceCompletion()
    return SemanticChoiceCompletion(intents=(MoveObjectsSimultaneouslyIntent(
        actor=actor, object_refs=(source.ref,), expected_zones=('battlefield',),
        destination='graveyard', reason=effect['_stack_label'],
        transition_kind=ZoneTransitionKind.SACRIFICE, controlled_only=True,
    ),))


def prepare_source_maintenance(
    effect: Mapping[str, Any], context: SemanticChoiceContext,
) -> SemanticChoicePreparation:
    local = {'_choice_actor', '_stack_label', '_source_logical_object_id', '_legal_objects'}
    restored = bool(set(effect) & local)
    spec = _validated(effect, context.actor, context.query, continuation=restored)
    current = context.query.object(context.source_ref, zones=('battlefield',)) if context.source_ref else None
    bound_source = context.source_ref if current is not None and (
        not current.phased_out and current.logical_object_id == context.source_logical_object_id
    ) else None
    if context.actor != context.stack_controller or effect['source'] != bound_source:
        raise SemanticChoiceError('Source maintenance stack identity changed')
    identity = context.source_logical_object_id
    if type(identity) is not str or not identity:
        raise SemanticChoiceError('Source maintenance lost its trigger incarnation')
    if restored and (
        effect['_source_logical_object_id'] != identity
        or effect['_choice_actor'] != context.actor
        or effect['_stack_label'] != context.stack_label
    ):
        raise SemanticChoiceError('Source maintenance continuation identity changed')
    stored = {
        **dict(effect), '_choice_actor': context.actor, '_stack_label': context.stack_label,
        '_source_logical_object_id': identity, '_legal_objects': [],
    }
    if spec.payment is None:
        completion = _declined_source_sacrifice(stored, context.query)
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(stored),
            preparation_intents=completion.intents,
            auto_continue=AutoContinue(reason='Resolve the mandatory source sacrifice.'),
        )
    return prepare_fixed_payment_cost(spec.payment, stored, context)


def complete_source_maintenance(
    continuation: SemanticChoiceContinuation, response: Mapping[str, Any],
    query: SemanticChoiceQuery,
) -> SemanticChoiceCompletion:
    effect = continuation.effect
    actor = effect.get('_choice_actor')
    spec = _validated(effect, actor, query, continuation=True)
    if actor != continuation.semantic_frame.controller or spec.payment is None:
        raise SemanticChoiceError('Source maintenance has no authorized payment choice')
    completion = complete_fixed_payment_cost(spec.payment, effect, response, query)
    return completion if completion.intents else _declined_source_sacrifice(effect, query)
