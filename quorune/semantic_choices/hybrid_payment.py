from __future__ import annotations

"""Offer and commit one affordable equivalent of a fixed hybrid price."""

from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import PayManaCostIntent
from .model import ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest


def prepare_hybrid_payment(spec, effect, context):
    actor = context.actor
    options = tuple(option for option in spec.hybrid_mana_options
        if context.query.cost_is_affordable(actor, option.requirements))
    choices = []
    for option in options:
        cost = option.compact()["m"]
        generic = cost.get("GENERIC", 0)
        label = (f"{{{generic}}}" if generic else "") + "".join(
            f"{{{color}}}" * cost.get(color, 0) for color in "WUBRGC")
        choices.append({"id": option.option_id, "label": f"Pay {label}", "cost": cost})
    choices.append({"id": "decline", "label": "Decline"})
    legal_options = tuple(option.option_id for option in options)
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(prompt="Choose a hybrid mana payment or decline.",
            choice=ScalarChoice(field_name="choice", legal_values=(*legal_options, "decline")),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": effect["op"],
                "payment_kind": spec.kind, "payable": bool(options), "options": choices})),
        continuation_effect=FrozenMap({**effect, "_choice_actor": actor, "_stack_label": context.stack_label,
            "_legal_objects": [], "_legal_mana_options": legal_options}),
    )


def complete_hybrid_payment(spec, effect, response, query):
    if set(response) - {"choice", "action", "action_id", "a", "cap", "reason", "plan", "next", "choice_schema"}:
        raise SemanticChoiceError("Hybrid payment response has unknown fields")
    options = {option.option_id: option for option in spec.hybrid_mana_options}
    legal = effect["_legal_mana_options"]
    if (not isinstance(legal, (tuple, list)) or any(type(key) is not str or key not in options for key in legal)
        or len(legal) != len(set(legal))):
        raise SemanticChoiceError("Hybrid payment continuation has invalid offered options")
    selected = response.get("choice")
    if selected == "decline":
        return SemanticChoiceCompletion()
    if type(selected) is not str or selected not in legal:
        raise SemanticChoiceError("Choose an offered hybrid payment or decline")
    actor = effect["_choice_actor"]
    option = options[selected]
    if not query.cost_is_affordable(actor, option.requirements):
        raise SemanticChoiceError("Selected hybrid payment is no longer payable")
    intent = PayManaCostIntent(actor=actor, player=actor, requirements=option.requirements,
        reason=effect["_stack_label"], event_code="effect.optional_mana.paid", message="Optional hybrid mana cost paid.",
        details=FrozenMap({"cost": dict(option.requirements), "option": selected}))
    return SemanticChoiceCompletion(intents=(intent,))
