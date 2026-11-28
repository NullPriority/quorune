from __future__ import annotations

"""Strict typed lowering for fixed counter-removal effects."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..counter_removal import (
    ALL_PERMANENT_COUNTERS_THEN_TOKEN_EXILE_FIELD, AllPermanentCountersThenTokenExileIntent,
    validate_all_permanent_counters_then_token_exile,
)
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .direct_target_fields import validate_direct_target_effect
from .intents import (
    IntentPlan,
    RemoveAllCountersIntent,
    RemoveCountersIntent,
)


def execute_counter_removal(sink, intent: RemoveCountersIntent):
    result = sink.remove_counters_intent(intent)
    if intent.gain_life_from_removed:
        from ..rules.upkeep_power_counter_balance import life_after_upkeep_counter_removal

        return life_after_upkeep_counter_removal(result)
    return result


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
        from ..rules.upkeep_power_counter_balance import REMOVAL_FOLLOWUP

        unavailable = effect.get('card', '') is None
        gain_life = REMOVAL_FOLLOWUP in effect
        if gain_life and (unavailable or type(effect[REMOVAL_FOLLOWUP]) is not int or effect[REMOVAL_FOLLOWUP] != 1
                          or context.source is None or context.source.card_ref != effect.get("card")
                          or context.source.card_ref != effect.get("source")):
            raise SemanticNodeError("Life from counter removal requires the original resolving source")
        direct = {key: value for key, value in effect.items() if key != REMOVAL_FOLLOWUP} if gain_life else effect
        fields = validate_direct_target_effect(
            {**direct, 'card': '$source.zone_object'} if unavailable else direct,
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
                gain_life_from_removed=gain_life,
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
        if ALL_PERMANENT_COUNTERS_THEN_TOKEN_EXILE_FIELD in effect:
            try:
                source = validate_all_permanent_counters_then_token_exile(effect, resolved=True)
                if context.source is None or source != (context.source.card_ref or context.source.stack_ref):
                    raise ValueError("Global counter removal requires the original spell source")
                intent = AllPermanentCountersThenTokenExileIntent(context.actor, source, context.default_reason)
            except ValueError as exc:
                raise SemanticNodeError(str(exc)) from exc
            return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))
        named = 'schema_version' in effect or 'counter' in effect
        unavailable = named and effect.get('card', '') is None
        counter_name = None
        if named:
            from ..counter_names import normalized_counter_name
            counter_name = effect.get('counter')
            if (type(effect.get('schema_version')) is not int or effect['schema_version'] not in (2, 3)
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
