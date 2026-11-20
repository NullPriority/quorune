"""Resolve a public counter-kind choice before preparing one existing removal intent."""
from collections.abc import Mapping
from dataclasses import dataclass
from ..chosen_counter_removal_model import (
    ChosenCounterRemovalSpec, CHOSEN_COUNTER_REMOVAL_OPERATION,
    CHOSEN_COUNTER_REMOVAL_CAPABILITY, CHOSEN_COUNTER_REMOVAL_FIELDS,
)
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import RemoveCountersIntent
from .model import (AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
                    SemanticChoicePreparation, SemanticChoiceRequest)

_LOCAL = frozenset({'_chooser', '_target_identity', '_offered_kinds'})
_RESPONSE = frozenset({'counter_kind', 'action', 'action_id', 'a', 'cap', 'reason', 'plan',
                       'next', 'choice_schema', 'label'})


def _target(query, ref):
    row = query.object(ref, zones=('battlefield',)) if ref is not None else None
    if row is None or row.phased_out:return None
    if any(type(value) is not str or not value for value in (row.object_id, row.logical_object_id)):
        raise SemanticChoiceError('The counter recipient requires its current object incarnation')
    return row


def _counter_kinds(target):
    if target is None:return ()
    if any(type(name) is not str or not name or type(count) is not int or count < 0
           for name, count in target.counters.items()):
        raise SemanticChoiceError('Public counters require named nonnegative exact counts')
    return tuple(sorted(name for name, count in target.counters.items() if count > 0))


@dataclass(frozen=True, slots=True)
class ChosenCounterRemovalHandler:
    operation: str = CHOSEN_COUNTER_REMOVAL_OPERATION
    handler_id: str = 'choice.public.chosen-counter-removal.v1'
    schema_version: int = 1
    rule_references: tuple[str, ...] = ('CR 101.3', 'CR 122.1', 'CR 122.2', 'CR 608.2b', 'CR 608.2d', 'CR 609.3')
    capability_dependencies: tuple[str, ...] = (CHOSEN_COUNTER_REMOVAL_CAPABILITY, 'counter.removal.fixed_effect')
    continuation_fields: tuple[str, ...] = tuple(sorted(CHOSEN_COUNTER_REMOVAL_FIELDS | _LOCAL))
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ('prompt', 'legal_actions.choice_schema.legal_values')
    mutation_path: tuple[str, ...] = ('RemoveCountersIntent', 'counter_removal.commit_counter_removal_effect')
    replay_fixture: str = 'chosen-counter-removal'
    test_modules: tuple[str, ...] = ('tests.test_chosen_counter_removal',)

    def prepare(self, effect, context):
        try:ref, _ = ChosenCounterRemovalSpec.decode(effect, materialized=True)
        except (TypeError, ValueError) as exc:raise SemanticChoiceError(str(exc)) from exc
        if context.actor != context.stack_controller:
            raise SemanticChoiceError('The resolving controller chooses the counter kind')
        target = _target(context.query, ref)
        kinds = _counter_kinds(target)
        local = FrozenMap({**dict(effect), '_chooser': context.actor, '_offered_kinds': kinds,
            '_target_identity': {'object_id': target.object_id, 'logical_object_id': target.logical_object_id}
            if target is not None else None})
        if not kinds:
            return SemanticChoicePreparation(request=None, continuation_effect=local,
                auto_continue=AutoContinue(reason='The original permanent has no counter to remove'))
        return SemanticChoicePreparation(continuation_effect=local,
            request=SemanticChoiceRequest(prompt='Choose a counter to remove.',
                choice=ScalarChoice(field_name='counter_kind', legal_values=kinds),
                public_context=FrozenMap({'stack': context.stack_ref, 'operation': self.operation, 'target': ref})))

    def complete(self, continuation, response, query):
        effect = continuation.effect
        if set(effect) != CHOSEN_COUNTER_REMOVAL_FIELDS | _LOCAL or set(response) - _RESPONSE:
            raise SemanticChoiceError('Chosen counter removal fields changed')
        try:ref, source = ChosenCounterRemovalSpec.decode(
            {key: effect[key] for key in CHOSEN_COUNTER_REMOVAL_FIELDS}, materialized=True)
        except (TypeError, ValueError) as exc:raise SemanticChoiceError(str(exc)) from exc
        actor = effect['_chooser']
        stack = query.stack_object(continuation.stack_ref)
        if (stack is None or stack.controller != actor or actor != continuation.semantic_frame.controller
                or actor not in query.active_seats):
            raise SemanticChoiceError('The original resolving controller must choose the counter')
        target = _target(query, ref)
        identity = effect['_target_identity']
        if (not isinstance(identity, Mapping) or set(identity) != {'object_id', 'logical_object_id'}
                or target is None or (target.object_id, target.logical_object_id)
                != (identity['object_id'], identity['logical_object_id'])):
            raise SemanticChoiceError('The counter recipient changed incarnation')
        offered = effect['_offered_kinds']
        if (not isinstance(offered, (tuple, list)) or not offered
                or any(type(kind) is not str or not kind for kind in offered)
                or tuple(offered) != tuple(sorted(set(offered)))):
            raise SemanticChoiceError('The counter choice lost its original offered kinds')
        chosen = response.get('counter_kind')
        if type(chosen) is not str or chosen not in offered or chosen not in _counter_kinds(target):
            raise SemanticChoiceError('Choose one currently present offered counter kind')
        return SemanticChoiceCompletion(intents=(RemoveCountersIntent(actor=actor, object_ref=ref,
            counter_name=chosen, amount=1, reason='Remove one chosen counter', source_ref=source),))
