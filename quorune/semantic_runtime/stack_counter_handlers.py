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
        destination = effect.get(COUNTER_DESTINATION_FIELD)
        destination_present = COUNTER_DESTINATION_FIELD in effect
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
            additional_allowed_fields=(COUNTER_DESTINATION_FIELD,) if destination_present else (),
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
                ),
            ),
        )


STACK_COUNTER_HANDLERS = (CounterStackTargetHandler(),)


__all__ = ["CounterStackTargetHandler", "STACK_COUNTER_HANDLERS"]
