"""Version-twelve Recover delegates payment legality and canonical payment intents."""
from dataclasses import replace
from ..replacement.immutable import FrozenMap
from ..rules.recover import recover_payment_spec, recover_result_effect
from .fixed_effect_payment import prepare_fixed_payment_cost, complete_fixed_payment_cost
from .model import SemanticChoiceCompletion, SemanticChoiceError
from ..rules.fractional_life_payment import FractionalLifeRecoverSpec


def prepare_recover(effect, context):
    try:
        spec = recover_payment_spec(effect, actor=context.actor)
    except (ValueError, TypeError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    identity = context.source_logical_object_id
    if (context.actor != context.stack_controller or context.actor not in context.query.active_seats
            or type(identity) is not str or not identity):
        raise SemanticChoiceError('Recover lost its original source or trigger controller')
    if isinstance(spec, FractionalLifeRecoverSpec):
        from .fractional_life_payment import prepare_fractional_life_cost
        prepared = prepare_fractional_life_cost(spec.payment, effect, context)
    else:prepared = prepare_fixed_payment_cost(spec.payment, effect, context)
    stored = FrozenMap({**dict(prepared.continuation_effect), '_recover_identity': identity})
    return replace(prepared, continuation_effect=stored)


def complete_recover(continuation, response, query):
    effect = continuation.effect; actor = effect.get('_choice_actor')
    try:
        spec = recover_payment_spec(effect, actor=actor, continuation=True)
    except (ValueError, TypeError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if (actor != continuation.semantic_frame.controller or actor not in query.active_seats
            or type(effect['_recover_identity']) is not str or not effect['_recover_identity']
            or type(effect['_stack_label']) is not str or not effect['_stack_label']
            or effect['_legal_objects'] not in ((), [])):
        raise SemanticChoiceError('Recover continuation identity changed')
    if isinstance(spec, FractionalLifeRecoverSpec):
        from .fractional_life_payment import complete_fractional_life_cost
        completion = complete_fractional_life_cost(spec.payment, effect, response, query)
    else:completion = complete_fixed_payment_cost(spec.payment, effect, response, query)
    result = recover_result_effect(actor=actor, identity=effect['_recover_identity'], paid=bool(completion.intents))
    return SemanticChoiceCompletion(intents=completion.intents, prepend_effects=(FrozenMap(result),))
