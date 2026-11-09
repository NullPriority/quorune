from __future__ import annotations

"""The existing fixed cost choice creates a consequence, never executes it."""

from dataclasses import replace

from ..creature_subtypes import canonical_creature_subtype
from ..reflexive_trigger_model import QueueReflexiveTriggerIntent, ReflexivePaidCreatureSnapshot
from ..scalar_effect_amount_model import PaidCreatureStatSnapshot
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
from ..zone_trigger_events import ZoneTransitionKind
from ..rules.reflexive_payment_shapes import reflexive_payment_parts, reflexive_program_capabilities
from ..replacement.immutable import FrozenMap
from .fixed_effect_payment import complete_fixed_payment_cost, prepare_fixed_payment_cost
from .model import SemanticChoiceCompletion, SemanticChoiceError


_LOCAL = {'_choice_actor', '_stack_label', '_legal_objects', '_parent_stack_ref'}


def _validated(effect, actor, query, *, continuation):
    raw = {key: value for key, value in effect.items() if key not in _LOCAL}
    if set(effect) != set(raw) | (_LOCAL if continuation else set()):
        raise SemanticChoiceError('Reflexive payment continuation fields are malformed')
    try:
        payment, program = reflexive_payment_parts(raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if not reflexive_program_capabilities(program):
        raise SemanticChoiceError('Reflexive consequences require independently closed effect ownership')
    if actor not in query.active_seats or raw['player'] != actor:
        raise SemanticChoiceError('Reflexive payment controller changed')
    if (payment.kind in {'discard','exile','mana_discard'} and payment.predicate.owner != actor
            or payment.kind in {'sacrifice','tap','blight'} and payment.predicate.controller != actor):
        raise SemanticChoiceError('Reflexive payment object relation changed')
    if continuation:
        if any(type(effect[key]) is not str or not effect[key]
               for key in ('_choice_actor', '_stack_label', '_parent_stack_ref')):
            raise SemanticChoiceError('Reflexive payment frame identity is malformed')
        legal = effect['_legal_objects']
        if (not isinstance(legal, (list, tuple)) or any(not isinstance(row, dict) and not isinstance(row, FrozenMap) for row in legal)
                or any(set(row) != {'ref', 'logical_object_id'}
                       or any(type(value) is not str or not value for value in row.values()) for row in legal)
                or len({row['ref'] for row in legal}) != len(legal)):
            raise SemanticChoiceError('Reflexive payment candidates are malformed')
    return payment, program


def prepare_reflexive_payment(effect, context):
    restored = bool(set(effect) & _LOCAL)
    payment, _ = _validated(effect, context.actor, context.query, continuation=restored)
    if restored and effect['_parent_stack_ref'] != context.stack_ref:
        raise SemanticChoiceError('Reflexive payment parent frame changed')
    preparation = prepare_fixed_payment_cost(payment, effect, context)
    return replace(preparation, continuation_effect=FrozenMap({
        **dict(preparation.continuation_effect), '_parent_stack_ref': context.stack_ref,
    }))


def _paid_creature_snapshot(intents, actor, query):
    if len(intents) != 1:
        raise SemanticChoiceError('Reflexive paid power requires exactly one sacrifice')
    intent = intents[0]
    if (not isinstance(intent, MoveObjectsSimultaneouslyIntent) or intent.actor != actor
            or intent.transition_kind is not ZoneTransitionKind.SACRIFICE or len(intent.object_refs) != 1
            or intent.expected_zones != ('battlefield',) or not intent.controlled_only):
        raise SemanticChoiceError('Reflexive paid power requires its selected sacrifice intent')
    creature = query.object(intent.object_refs[0], zones=('battlefield',))
    if creature is None or creature.controller != actor or 'creature' not in creature.types:
        raise SemanticChoiceError('The selected sacrifice is no longer a controlled creature')
    try:
        stats = PaidCreatureStatSnapshot(creature.logical_object_id, creature.effective_power, creature.effective_toughness)
        subtypes = tuple(sorted({subtype for value in creature.subtypes
                                 if (subtype := canonical_creature_subtype(value)) is not None}))
        return ReflexivePaidCreatureSnapshot(stats, subtypes)
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc


def complete_reflexive_payment(continuation, response, query):
    effect = continuation.effect
    actor = effect.get('_choice_actor')
    payment, program = _validated(effect, actor, query, continuation=True)
    if effect['_parent_stack_ref'] != continuation.stack_ref:
        raise SemanticChoiceError('Reflexive payment parent frame changed')
    completion = complete_fixed_payment_cost(payment, effect, response, query)
    if not completion.intents:
        return completion
    paid_creature = _paid_creature_snapshot(completion.intents, actor, query) if program.uses_paid_creature else None
    return SemanticChoiceCompletion(intents=(*completion.intents,
        QueueReflexiveTriggerIntent(controller=actor, parent_stack_ref=continuation.stack_ref,
            program=FrozenMap(program.to_dict()), paid_creature=paid_creature),))
