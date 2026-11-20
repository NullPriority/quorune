"""Reuse cost legality and movement, sealing selected public stats before commitment."""
from ..rules.optional_sacrifice_stats import optional_sacrifice_stat_spec
from ..scalar_effect_amount_model import PaidCreatureStatSnapshot
from ..replacement.immutable import FrozenMap, thaw_value
from .fixed_effect_payment import prepare_fixed_payment_cost, complete_fixed_payment_cost
from .model import SemanticChoiceCompletion, SemanticChoiceError


def prepare_optional_sacrifice_stat_result(effect, context):
    try:
        payment, _ = optional_sacrifice_stat_spec(effect, actor=context.actor)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if context.actor != context.stack_controller or context.actor not in context.query.active_seats:
        raise SemanticChoiceError('Sacrifice stat payment lost its ability controller')
    return prepare_fixed_payment_cost(payment, effect, context)


def complete_optional_sacrifice_stat_result(continuation, response, query):
    effect = continuation.effect; actor = effect.get('_choice_actor')
    try:
        payment, stat = optional_sacrifice_stat_spec(effect, actor=actor, continuation=True)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if actor != continuation.semantic_frame.controller or actor not in query.active_seats:
        raise SemanticChoiceError('Sacrifice stat continuation lost its paying controller')
    paid = complete_fixed_payment_cost(payment, effect, response, query)
    if not paid.intents:
        return paid
    ref, = response['cards']; row = query.object(ref, zones=('battlefield',))
    legal = {r['ref']: r['logical_object_id'] for r in effect['_legal_objects']}
    if row is None or row.logical_object_id != legal.get(ref):
        raise SemanticChoiceError('Sacrifice stat fact changed its original paid incarnation')
    try:
        snapshot = PaidCreatureStatSnapshot(row.logical_object_id, row.effective_power, row.effective_toughness)
        amount = stat.amount(snapshot)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    result = {**thaw_value(effect['result']), 'quantity': amount}
    return SemanticChoiceCompletion(intents=paid.intents,
        prepend_effects=(FrozenMap(result),) if amount else ())
