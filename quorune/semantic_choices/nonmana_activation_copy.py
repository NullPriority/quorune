"""Pay one ordinary mana price before using the canonical ability-copy choice."""

from ..rules.nonmana_activation_copy_model import ACTIVATION_COPY_CONTEXT, ActivationCopySource
from ..replacement.immutable import FrozenMap
from ..rules.nonmana_activation_copy import PAYMENT, validate_nonmana_activation_payment
from .fixed_effect_payment import complete_fixed_payment_cost, prepare_fixed_payment_cost
from .model import AutoContinue, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation


def _copy_after_payment(effect, actor, *, complete=False):
    local = {"_choice_actor", "_legal_objects", "_stack_label"} if complete else set()
    try:
        copy, = effect["effects"]
        source = ActivationCopySource.from_dict(copy[ACTIVATION_COPY_CONTEXT])
        original = source.item()
        if complete and (not local <= set(effect) or effect["_choice_actor"] != actor):
            raise ValueError("The activation copy payment is missing its original chooser")
        validate_nonmana_activation_payment({key: value for key, value in effect.items() if key not in local},
                                           actor=actor, target=original.ref, source=source.to_dict())
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return FrozenMap({"op": "copy_stack_item", "stack": original.ref, "copy_source": source.to_dict()})


def prepare_nonmana_activation_copy_payment(effect, context):
    if context.actor != context.stack_controller or context.actor not in context.query.active_seats:
        raise SemanticChoiceError("Only the resolving activation-copy controller can pay")
    _copy_after_payment(effect, context.actor)
    prepared = prepare_fixed_payment_cost(PAYMENT, effect, context)
    if not prepared.request.public_context["payable"]:
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue("The activation-copy cost cannot be paid"))
    return prepared


def complete_nonmana_activation_copy_payment(continuation, response, query):
    actor = continuation.semantic_frame.controller
    if actor not in query.active_seats:
        raise SemanticChoiceError("The activation-copy controller left the game")
    effect = continuation.effect
    result = _copy_after_payment(effect, actor, complete=True)
    settled = complete_fixed_payment_cost(PAYMENT, effect, response, query)
    return SemanticChoiceCompletion(intents=settled.intents, prepend_effects=(result,) if settled.intents else ())
