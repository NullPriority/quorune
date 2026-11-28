"""Offer and commit an optional generic X while retaining accepted zero."""

from ..reflexive_trigger_model import QueueReflexiveTriggerIntent
from ..replacement.immutable import FrozenMap
from ..rules.entrant_x_reflexive import (
    entrant_x_child, entrant_x_payment_program_dependencies, original_entrant_receipt,
    uses_entrant_x_payment, validate_entrant_x_effect,
)
from ..semantic_runtime.intents import PayManaCostIntent
from .model import (
    OptionalVariableManaChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation,
    SemanticChoiceRequest,
)


def _maximum_payable_generic(host, payer):
    payable, upper = 0, 1
    while host._cost_is_affordable(payer, {"GENERIC": upper}):
        payable, upper = upper, upper * 2
    while upper - payable > 1:
        candidate = (upper + payable) // 2
        if host._cost_is_affordable(payer, {"GENERIC": candidate}):
            payable = candidate
        else:
            upper = candidate
    return payable


def bind_entrant_x_payment(host, item, effect):
    if not uses_entrant_x_payment(effect):
        return effect
    actor = item.controller
    validate_entrant_x_effect(effect, payer=actor)
    original_entrant_receipt(item)
    program = host.semantics.get(item.semantic_key)
    if program is None or not host.semantic_program_is_current_trusted(program):
        raise ValueError("Entrant payment requires its admitted resolving trigger")
    required = entrant_x_payment_program_dependencies(program)
    if not required or not set(required) <= set(program.capability_dependencies):
        raise ValueError("Entrant payment lost its original payment authority")
    return {**effect, "_payable_x": _maximum_payable_generic(host, actor)}


def _quoted_x_payment(effect, *, payer):
    try:
        static = {key: value for key, value in effect.items() if key != "_payable_x"}
        validate_entrant_x_effect(static, payer=payer)
        maximum = effect.get("_payable_x")
        if type(maximum) is not int or maximum < 0:
            raise ValueError("Entrant payment lost its sealed payable amount")
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return maximum


def prepare_entrant_x_payment(effect, context):
    if context.actor != context.stack_controller or context.actor not in context.query.active_seats:
        raise SemanticChoiceError("Entrant payment requires its resolving controller")
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="You may pay X mana to trigger counters on the creature that entered.",
            choice=OptionalVariableManaChoice(_quoted_x_payment(effect, payer=context.actor)),
            public_context=FrozenMap({"stack": context.stack_ref, "payment_kind": "mana"}),
        ),
        continuation_effect=FrozenMap(effect),
    )


def complete_entrant_x_payment(continuation, response, query):
    actor = continuation.semantic_frame.controller
    if actor not in query.active_seats:
        raise SemanticChoiceError("Entrant payment requires its resolving controller")
    bound = _quoted_x_payment(continuation.effect, payer=actor)
    pay, amount = response.get("pay"), response.get("x")
    if type(pay) is not bool:
        raise SemanticChoiceError("Choose whether to pay the optional mana cost")
    if not pay:
        if amount is not None and (type(amount) is not int or amount != 0):
            raise SemanticChoiceError("Declining the payment cannot supply a positive amount")
        return SemanticChoiceCompletion()
    if type(amount) is not int or not 0 <= amount <= bound:
        raise SemanticChoiceError("Choose a payable nonnegative integer X")
    requirements = FrozenMap({"GENERIC": amount})
    payment = (PayManaCostIntent(
        actor=actor, player=actor, requirements=requirements, reason=continuation.note or "Reflexive entrant payment",
        event_code="optional_cost.paid", message=f"{actor} paid {amount} mana.",
        details=FrozenMap({"cost": requirements, "stack": continuation.stack_ref}),
    ),) if amount else ()
    return SemanticChoiceCompletion(intents=(*payment, QueueReflexiveTriggerIntent(
        controller=actor, parent_stack_ref=continuation.stack_ref,
        program=FrozenMap(entrant_x_child().to_dict()), paid_mana=amount,
    )))
