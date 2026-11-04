from __future__ import annotations

"""Strict typed lowering for fixed counter-removal effects."""

from dataclasses import dataclass
from typing import Any, Mapping

from .context import ReadOnlyHandlerContext, SemanticNodeError
from .direct_target_fields import validate_direct_target_effect
from .intents import (
    IntentPlan,
    RemoveAllCountersIntent,
    RemoveCountersIntent,
)


@dataclass(frozen=True, slots=True)
class FixedCounterRemovalHandler:
    handler_id: str = "generic.fixed-counter-removal.v1"
    schema_version: int = 1
    family: str = "effect.counter-removal"
    operation: str = "remove_counters"
    rule_references: tuple[str, ...] = (
        "101.3",
        "115.3",
        "115.6",
        "122.1",
        "608.2b",
        "608.2c",
        "609.3",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.removal.fixed_effect",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        unavailable = effect.get('card', '') is None
        fields = validate_direct_target_effect(
            {**effect, 'card': '$source.zone_object'} if unavailable else effect,
            context,
            operation=self.operation,
            reference_field="card",
            family_label="Counter removal",
            allow_replacement_selections=False,
            additional_allowed_fields=("counter", "amount", "source"),
        )
        counter_name = effect.get("counter")
        if type(counter_name) is not str or not counter_name.strip():
            raise SemanticNodeError(
                "Counter removal requires one nonempty counter name"
            )
        amount = effect.get("amount")
        if type(amount) is not int or amount <= 0:
            raise SemanticNodeError(
                "Counter removal amount must be a positive exact integer"
            )
        source_ref = effect.get("source")
        if type(source_ref) is not str or not source_ref:
            raise SemanticNodeError(
                "Counter removal requires one nonempty source reference"
            )
        if unavailable:
            return IntentPlan(operation=self.operation, handler_id=self.handler_id,
                              intents=(), result_shape='by_player')
        try:
            intent = RemoveCountersIntent(
                actor=context.actor,
                object_ref=fields.object_ref,
                counter_name=counter_name,
                amount=amount,
                reason=fields.reason,
                source_ref=source_ref,
            )
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(intent,),
        )


@dataclass(frozen=True, slots=True)
class AllCounterRemovalHandler:
    handler_id: str = "generic.all-counter-removal.v1"
    schema_version: int = 1
    family: str = "effect.counter-removal"
    operation: str = "remove_all_counters"
    rule_references: tuple[str, ...] = (
        "101.3",
        "115.3",
        "115.6",
        "122.1",
        "608.2b",
        "608.2c",
        "609.3",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.removal.all_effect",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        named = 'schema_version' in effect or 'counter' in effect
        unavailable = named and effect.get('card', '') is None
        counter_name = None
        if named:
            from ..counter_names import normalized_counter_name
            counter_name = effect.get('counter')
            if (type(effect.get('schema_version')) is not int or effect['schema_version'] != 2
                    or type(counter_name) is not str or not counter_name.strip()
                    or normalized_counter_name(counter_name) != counter_name):
                raise SemanticNodeError('Named-kind counter removal requires its version and canonical name')
            descriptor = {key: value for key, value in effect.items() if key not in {'schema_version', 'counter'}}
        else:
            descriptor = effect
        fields = validate_direct_target_effect(
            {**descriptor, 'card': '$source.zone_object'} if unavailable else descriptor,
            context,
            operation=self.operation,
            reference_field="card",
            family_label="All-counter removal",
            allow_replacement_selections=False,
            additional_allowed_fields=("source",),
        )
        source_ref = effect.get("source")
        if type(source_ref) is not str or not source_ref:
            raise SemanticNodeError(
                "All-counter removal requires one nonempty source reference"
            )
        if unavailable:
            return IntentPlan(operation=self.operation, handler_id=self.handler_id,
                              intents=(), result_shape='by_player')
        try:
            intent = RemoveAllCountersIntent(
                actor=context.actor,
                object_ref=fields.object_ref,
                reason=fields.reason,
                source_ref=source_ref,
                counter_name=counter_name,
            )
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(intent,),
        )


COUNTER_REMOVAL_HANDLERS = (
    FixedCounterRemovalHandler(),
    AllCounterRemovalHandler(),
)


__all__ = [
    "COUNTER_REMOVAL_HANDLERS",
    "AllCounterRemovalHandler",
    "FixedCounterRemovalHandler",
]
