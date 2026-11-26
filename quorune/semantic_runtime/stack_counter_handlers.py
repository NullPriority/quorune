from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .context import ReadOnlyHandlerContext, SemanticNodeError
from ..zone_trigger_events import COUNTER_DESTINATION_FIELD, counter_destination_fields
from .direct_target_fields import validate_direct_target_effect
from .intents import CounterStackIntent, IntentPlan


@dataclass(frozen=True, slots=True)
class CounterStackTargetHandler:
    handler_id: str = "generic.counter-stack-target.v1"
    schema_version: int = 1
    family: str = "effect.stack-counter"
    operation: str = "counter_stack_target"
    rule_references: tuple[str, ...] = (
        "115.1",
        "608.2b",
        "701.6",
        "701.6a",
        "701.6b",
    )
    capability_dependencies: tuple[str, ...] = (
        "stack.counter.effect",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        from ..stack_counter import (
            COUNTERED_ABILITY_ACTIVATION_LOCK_FIELD, COUNTERED_ABILITY_SOURCE_LOSS_FIELD,
            CounterActivationProhibition, CounteredAbilityActivationLock,
        )

        source_loss = COUNTERED_ABILITY_SOURCE_LOSS_FIELD in effect
        destination = effect.get(COUNTER_DESTINATION_FIELD)
        destination_present = COUNTER_DESTINATION_FIELD in effect
        activation_prohibition = None
        if COUNTERED_ABILITY_ACTIVATION_LOCK_FIELD in effect:
            if source_loss or destination_present or context.source is None:
                raise SemanticNodeError("Activation prohibition requires an ordinary counter of one bound ability")
            try:
                mode = CounteredAbilityActivationLock(effect[COUNTERED_ABILITY_ACTIVATION_LOCK_FIELD])
                activation_prohibition = CounterActivationProhibition(context.source, mode)
            except (TypeError, ValueError) as exc:
                raise SemanticNodeError("Countered ability activation prohibition is malformed") from exc
        if source_loss:
            version = effect[COUNTERED_ABILITY_SOURCE_LOSS_FIELD]
            if type(version) is not int or version != 1 or destination_present or context.source is None:
                raise SemanticNodeError("Countered ability source loss needs its exact bound source")
        if destination_present:
            try:
                counter_destination_fields(destination)
            except ValueError as exc:
                raise SemanticNodeError(str(exc)) from exc
        fields = validate_direct_target_effect(
            effect,
            context,
            operation=self.operation,
            reference_field="stack",
            family_label="Counter",
            allow_replacement_selections=destination_present,
            additional_allowed_fields=(
                (COUNTERED_ABILITY_SOURCE_LOSS_FIELD,) if source_loss
                else (COUNTERED_ABILITY_ACTIVATION_LOCK_FIELD,) if activation_prohibition is not None
                else (COUNTER_DESTINATION_FIELD,) if destination_present else ()
            ),
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                CounterStackIntent(
                    actor=context.actor,
                    stack_ref=fields.object_ref,
                    reason=fields.reason,
                    countered_by=context.actor,
                    counter_destination=destination,
                    replacement_selections=fields.replacement_selections,
                    ability_source_loss=context.source if source_loss else None,
                    activation_prohibition=activation_prohibition,
                ),
            ),
        )


STACK_COUNTER_HANDLERS = (CounterStackTargetHandler(),)


__all__ = ["CounterStackTargetHandler", "STACK_COUNTER_HANDLERS"]
