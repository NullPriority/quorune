"""Offer one finite order, then schedule the ordinary sacrifice stages."""

from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from ..rules.ordered_type_sacrifice import (
    ORDERED_TYPE_SACRIFICE_CAPABILITY, ORDERED_TYPE_SACRIFICE_OPERATION, ORDERED_TYPE_SACRIFICE_RULES,
    SACRIFICE_ORDER_CHOICES, ordered_sacrifice_stages, validate_ordered_type_sacrifice,
)
from .model import (
    ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest,
)


@dataclass(frozen=True, slots=True)
class OrderedTypeSacrificeHandler:
    operation: str = ORDERED_TYPE_SACRIFICE_OPERATION
    handler_id: str = "choice.permanent.fixed-type-sacrifice-order.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ORDERED_TYPE_SACRIFICE_RULES
    capability_dependencies: tuple[str, ...] = (ORDERED_TYPE_SACRIFICE_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("schema_version", "player", "_stack_ref")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("prompt", "legal_actions.choice_schema.legal_values")
    mutation_path: tuple[str, ...] = ("SemanticChoiceCompletion.prepend_effects", "choose_cards_apnap")
    replay_fixture: str = "ordered-permanent-type-sacrifices"
    test_modules: tuple[str, ...] = ("tests.test_ordered_permanent_type_sacrifices",)

    def prepare(self, effect, context):
        try:
            validate_ordered_type_sacrifice(effect, actor=context.actor)
            if context.actor != context.stack_controller or context.actor not in context.query.active_seats:
                raise ValueError("The spell's resolving controller chooses the sacrifice order")
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Choose the sacrifice order for artifacts, creatures, and lands.",
                choice=ScalarChoice("order", SACRIFICE_ORDER_CHOICES),
                public_context=FrozenMap({"stack": context.stack_ref, "operation": self.operation}),
            ),
            continuation_effect=FrozenMap({**effect, "_stack_ref": context.stack_ref}),
        )

    def complete(self, continuation, response, query):
        saved = continuation.effect
        actor = continuation.semantic_frame.controller
        effect = {key: value for key, value in saved.items() if key != "_stack_ref"}
        try:
            validate_ordered_type_sacrifice(effect, actor=actor)
            if saved.get("_stack_ref") != continuation.stack_ref or actor not in query.active_seats:
                raise ValueError("The sacrifice order lost its original resolving spell")
            stages = ordered_sacrifice_stages(actor, response.get("order"))
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoiceCompletion(prepend_effects=stages)
