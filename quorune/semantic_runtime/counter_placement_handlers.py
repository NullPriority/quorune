from __future__ import annotations

"""Strict typed lowering for fixed counter-placement effects."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..affected_permanents import (
    AffectedPermanentSetError,
    AffectedPermanentSetSpec,
    PermanentControllerRelation,
)
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .direct_target_fields import validate_direct_target_effect
from .intents import (
    CounterPlacementAmount,
    IntentPlan,
    PlaceCounterBatchIntent,
    PlaceCountersIntent,
    PlaceCountersOnSetIntent,
    PlaceCountersOnTargetsIntent,
    PlacePlayerCountersIntent,
)


_REASON_FIELD = "reason"


def _fixed_counter_group_intent(
    effect: Mapping[str, Any],
    context: ReadOnlyHandlerContext,
) -> PlaceCountersIntent:
    allowed = {
        "op",
        "cards",
        "counter",
        "amount",
        "source",
        _REASON_FIELD,
        "_replacement_selections",
    }
    unknown = sorted(set(effect) - allowed)
    if unknown:
        raise SemanticNodeError(
            "Counter placement group has unknown fields: "
            + ", ".join(unknown)
        )
    missing = sorted(
        {"op", "cards", "counter", "amount", "source"} - set(effect)
    )
    if missing:
        raise SemanticNodeError(
            "Counter placement group is missing fields: "
            + ", ".join(missing)
        )
    if effect.get("op") != "place_counters":
        raise SemanticNodeError("Counter placement group operation is unsupported")
    raw_cards = effect.get("cards")
    if not isinstance(raw_cards, (list, tuple)) or not 2 <= len(raw_cards) <= 3:
        raise SemanticNodeError(
            "Counter placement group requires two or three subject entries"
        )
    if any(
        value is not None and (type(value) is not str or not value)
        for value in raw_cards
    ):
        raise SemanticNodeError(
            "Counter placement group subjects must be public refs or unavailable"
        )
    counter_name = effect.get("counter")
    if type(counter_name) is not str or not counter_name.strip():
        raise SemanticNodeError(
            "Counter placement group requires one nonempty counter name"
        )
    amount = effect.get("amount")
    if type(amount) is not int or amount <= 0:
        raise SemanticNodeError(
            "Counter placement group amount must be a positive exact integer"
        )
    source_ref = effect.get("source")
    if type(source_ref) is not str or not source_ref:
        raise SemanticNodeError(
            "Counter placement group requires one nonempty source reference"
        )
    reason = effect.get(_REASON_FIELD, context.default_reason)
    if type(reason) is not str or not reason:
        raise SemanticNodeError("Counter placement group reason must be nonempty")
    raw_selections = effect.get("_replacement_selections", ())
    if raw_selections is None:
        raw_selections = ()
    if not isinstance(raw_selections, (list, tuple)):
        raise SemanticNodeError(
            "Counter placement group replacement selections must be an array"
        )
    try:
        return PlaceCountersIntent(
            actor=context.actor,
            object_refs=tuple(
                value for value in raw_cards if value is not None
            ),
            counter_name=" ".join(counter_name.casefold().split()),
            amount=amount,
            reason=reason,
            source_ref=source_ref,
            replacement_selections=tuple(raw_selections),
        )
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class FixedCounterPlacementHandler:
    handler_id: str = "generic.fixed-counter-placement.v1"
    schema_version: int = 1
    family: str = "effect.counter-placement"
    operation: str = "place_counters"
    rule_references: tuple[str, ...] = (
        "115.3",
        "115.6",
        "122.1",
        "122.1a",
        "122.6",
        "608.2c",
        "608.2f",
        "608.2h",
        "614.16",
        "616.1",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.producer.fixed_effect",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        if "cards" in effect:
            return IntentPlan(
                operation=self.operation,
                handler_id=self.handler_id,
                intents=(_fixed_counter_group_intent(effect, context),),
            )
        fields = validate_direct_target_effect(
            effect,
            context,
            operation=self.operation,
            reference_field="card",
            family_label="Counter placement",
            allow_replacement_selections=True,
            additional_allowed_fields=("counter", "amount", "source"),
        )
        counter_name = effect.get("counter")
        if type(counter_name) is not str or not counter_name.strip():
            raise SemanticNodeError(
                "Counter placement requires one nonempty counter name"
            )
        amount = effect.get("amount")
        if type(amount) is not int or amount < 0:
            raise SemanticNodeError(
                "Counter placement amount must be a nonnegative exact integer"
            )
        source_ref = effect.get("source")
        if type(source_ref) is not str or not source_ref:
            raise SemanticNodeError(
                "Counter placement requires one nonempty source reference"
            )
        if amount == 0:
            return IntentPlan(
                operation=self.operation,
                handler_id=self.handler_id,
                intents=(),
                result_shape="by_player",
            )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                PlaceCountersIntent(
                    actor=context.actor,
                    object_refs=(fields.object_ref,),
                    counter_name=" ".join(counter_name.casefold().split()),
                    amount=amount,
                    reason=fields.reason,
                    source_ref=source_ref,
                    replacement_selections=fields.replacement_selections,
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class FixedCounterPlacementBatchHandler:
    handler_id: str = "generic.fixed-counter-placement-batch.v1"
    schema_version: int = 1
    family: str = "effect.counter-placement-batch"
    operation: str = "place_counter_batch"
    rule_references: tuple[str, ...] = (
        "122.1",
        "122.1a",
        "122.6",
        "608.2c",
        "608.2h",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.producer.fixed_multikind_effect",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        fields = validate_direct_target_effect(
            effect,
            context,
            operation=self.operation,
            reference_field="card",
            family_label="Counter batch placement",
            allow_replacement_selections=True,
            additional_allowed_fields=("placements", "source"),
        )
        raw_placements = effect.get("placements")
        if not isinstance(raw_placements, (list, tuple)) or not 2 <= len(
            raw_placements
        ) <= 3:
            raise SemanticNodeError(
                "Counter batch placement requires two or three entries"
            )
        placements: list[CounterPlacementAmount] = []
        for index, raw in enumerate(raw_placements):
            if not isinstance(raw, Mapping) or set(raw) != {
                "counter",
                "amount",
            }:
                raise SemanticNodeError(
                    f"Counter batch placement entry {index} is malformed"
                )
            try:
                placements.append(
                    CounterPlacementAmount(
                        counter_name=raw.get("counter"),
                        amount=raw.get("amount"),
                    )
                )
            except (TypeError, ValueError) as exc:
                raise SemanticNodeError(str(exc)) from exc
        source_ref = effect.get("source")
        if type(source_ref) is not str or not source_ref:
            raise SemanticNodeError(
                "Counter batch placement requires one nonempty source reference"
            )
        try:
            intent = PlaceCounterBatchIntent(
                actor=context.actor,
                object_ref=fields.object_ref,
                placements=tuple(placements),
                reason=fields.reason,
                source_ref=source_ref,
                replacement_selections=fields.replacement_selections,
            )
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(intent,),
        )


@dataclass(frozen=True, slots=True)
class FixedCounterPlacementSetHandler:
    handler_id: str = "generic.fixed-counter-placement-set.v1"
    schema_version: int = 1
    family: str = "effect.counter-placement-set"
    operation: str = "place_counters_on_set"
    rule_references: tuple[str, ...] = (
        "122.1",
        "122.1a",
        "122.6",
        "608.2c",
        "608.2h",
        "614.16",
        "616.1",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.producer.fixed_permanent_set_effect",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        allowed = {
            "op",
            "source",
            "set",
            "counter",
            "amount",
            _REASON_FIELD,
            "_replacement_selections",
        }
        unknown = sorted(set(effect) - allowed)
        if unknown:
            raise SemanticNodeError(
                "Counter-set effect has unknown fields: "
                + ", ".join(unknown)
            )
        missing = sorted(
            {"op", "source", "set", "counter", "amount"} - set(effect)
        )
        if missing:
            raise SemanticNodeError(
                "Counter-set effect is missing fields: "
                + ", ".join(missing)
            )
        if effect.get("op") != self.operation:
            raise SemanticNodeError("Counter-set operation is unsupported")
        try:
            spec = AffectedPermanentSetSpec.from_dict(effect.get("set"))
        except (AffectedPermanentSetError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        if spec.controller_relation is PermanentControllerRelation.TARGET_PLAYER:
            context.query.require_active_seat(
                str(spec.target_controller or "")
            )
        counter_name = effect.get("counter")
        if type(counter_name) is not str or not counter_name.strip():
            raise SemanticNodeError(
                "Counter-set placement requires one nonempty counter name"
            )
        amount = effect.get("amount")
        if type(amount) is not int or amount <= 0:
            raise SemanticNodeError(
                "Counter-set amount must be a positive exact integer"
            )
        source_ref = effect.get("source")
        if type(source_ref) is not str or not source_ref:
            raise SemanticNodeError(
                "Counter-set placement requires one nonempty source reference"
            )
        raw_reason = effect.get(_REASON_FIELD)
        if raw_reason is not None and (
            type(raw_reason) is not str or not raw_reason
        ):
            raise SemanticNodeError(
                "Counter-set reason must be a nonempty string"
            )
        raw_selections = effect.get("_replacement_selections")
        if raw_selections is None:
            raw_selections = ()
        if not isinstance(raw_selections, (list, tuple)):
            raise SemanticNodeError(
                "Counter-set replacement selections must be an array"
            )
        try:
            intent = PlaceCountersOnSetIntent(
                actor=context.actor,
                spec=spec,
                counter_name=counter_name,
                amount=amount,
                reason=raw_reason or context.default_reason,
                source_ref=source_ref,
                replacement_selections=tuple(raw_selections),
            )
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(intent,),
        )


@dataclass(frozen=True, slots=True)
class FixedCounterPlacementTargetSetHandler:
    handler_id: str = "generic.fixed-counter-placement-target-set.v1"
    schema_version: int = 1
    family: str = "effect.counter-placement-target-set"
    operation: str = "place_counters_on_targets"
    rule_references: tuple[str, ...] = (
        "115.1",
        "115.3",
        "115.6",
        "122.1",
        "122.1a",
        "122.6",
        "608.2b",
        "614.16",
        "616.1",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.producer.fixed_permanent_target_set_effect",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        allowed = {
            "op",
            "cards",
            "maximum_targets",
            "counter",
            "amount",
            "source",
            _REASON_FIELD,
            "_replacement_selections",
        }
        unknown = sorted(set(effect) - allowed)
        if unknown:
            raise SemanticNodeError(
                "Counter-target effect has unknown fields: "
                + ", ".join(unknown)
            )
        missing = sorted(
            {
                "op",
                "cards",
                "maximum_targets",
                "counter",
                "amount",
                "source",
            }
            - set(effect)
        )
        if missing:
            raise SemanticNodeError(
                "Counter-target effect is missing fields: "
                + ", ".join(missing)
            )
        if effect.get("op") != self.operation:
            raise SemanticNodeError("Counter-target operation is unsupported")
        raw_cards = effect.get("cards")
        if not isinstance(raw_cards, (list, tuple)):
            raise SemanticNodeError(
                "Counter-target placement requires an array of targets"
            )
        cards = tuple(raw_cards)
        maximum = effect.get("maximum_targets")
        if (
            type(maximum) is not int
            or maximum <= 0
            or len(cards) > maximum
            or any(type(card) is not str or not card for card in cards)
            or len(cards) != len(set(cards))
        ):
            raise SemanticNodeError(
                "Counter-target placement requires unique targets within a positive maximum"
            )
        counter_name = effect.get("counter")
        if type(counter_name) is not str or not counter_name.strip():
            raise SemanticNodeError(
                "Counter-target placement requires one nonempty counter name"
            )
        amount = effect.get("amount")
        if type(amount) is not int or amount <= 0:
            raise SemanticNodeError(
                "Counter-target amount must be a positive exact integer"
            )
        source_ref = effect.get("source")
        if type(source_ref) is not str or not source_ref:
            raise SemanticNodeError(
                "Counter-target placement requires one nonempty source reference"
            )
        raw_reason = effect.get(_REASON_FIELD)
        if raw_reason is not None and (
            type(raw_reason) is not str or not raw_reason
        ):
            raise SemanticNodeError(
                "Counter-target reason must be a nonempty string"
            )
        raw_selections = effect.get("_replacement_selections")
        if raw_selections is None:
            raw_selections = ()
        if not isinstance(raw_selections, (list, tuple)):
            raise SemanticNodeError(
                "Counter-target replacement selections must be an array"
            )
        try:
            intent = PlaceCountersOnTargetsIntent(
                actor=context.actor,
                object_refs=cards,
                maximum_targets=maximum,
                counter_name=counter_name,
                amount=amount,
                reason=raw_reason or context.default_reason,
                source_ref=source_ref,
                replacement_selections=tuple(raw_selections),
            )
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(intent,),
        )


@dataclass(frozen=True, slots=True)
class FixedPlayerCounterPlacementHandler:
    handler_id: str = "generic.fixed-player-counter-placement.v1"
    schema_version: int = 1
    family: str = "effect.player-counter-placement"
    operation: str = "place_player_counters"
    rule_references: tuple[str, ...] = (
        "101.4",
        "107.14",
        "107.17",
        "115.1",
        "122.1",
        "608.2b",
        "608.2c",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.producer.fixed_player_effect",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        subject = effect.get("subjects")
        base_fields = {
            "op",
            "subjects",
            "counter",
            "amount",
            "source",
            _REASON_FIELD,
            "_replacement_selections",
        }
        allowed = base_fields | ({"target"} if subject == "target" else set())
        unknown = sorted(set(effect) - allowed)
        if unknown:
            raise SemanticNodeError(
                "Player counter effect has unknown fields: "
                + ", ".join(unknown)
            )
        if effect.get("op") != self.operation or subject not in {
            "controller",
            "target",
            "each-player",
            "each-opponent",
        }:
            raise SemanticNodeError(
                "Player counter placement subject is unsupported"
            )
        counter_name = effect.get("counter")
        if type(counter_name) is not str or not counter_name.strip():
            raise SemanticNodeError(
                "Player counter placement requires one nonempty counter name"
            )
        amount = effect.get("amount")
        if type(amount) is not int or amount <= 0:
            raise SemanticNodeError(
                "Player counter amount must be a positive exact integer"
            )
        source_ref = effect.get("source")
        if type(source_ref) is not str or not source_ref:
            raise SemanticNodeError(
                "Player counter placement requires one nonempty source reference"
            )
        reason = effect.get(_REASON_FIELD, context.default_reason)
        if type(reason) is not str or not reason:
            raise SemanticNodeError(
                "Player counter placement reason must be nonempty"
            )
        raw_selections = effect.get("_replacement_selections", ())
        if raw_selections is None:
            raw_selections = ()
        if not isinstance(raw_selections, (list, tuple)):
            raise SemanticNodeError(
                "Player counter replacement selections must be an array"
            )
        if subject == "controller":
            players = (context.query.require_active_seat(context.actor),)
        elif subject == "target":
            target = effect.get("target")
            if type(target) is not str or not target:
                raise SemanticNodeError(
                    "Targeted player counter placement requires one target"
                )
            players = (context.query.require_active_seat(target),)
        elif subject == "each-player":
            players = context.query.apnap_order
        else:
            players = tuple(
                player
                for player in context.query.apnap_order
                if player != context.actor
            )
        try:
            intent = PlacePlayerCountersIntent(
                actor=context.actor,
                player_ids=players,
                counter_name=counter_name,
                amount=amount,
                reason=reason,
                source_ref=source_ref,
                replacement_selections=tuple(raw_selections),
            )
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(intent,),
        )


COUNTER_PLACEMENT_HANDLERS = (
    FixedCounterPlacementHandler(),
    FixedCounterPlacementBatchHandler(),
    FixedCounterPlacementSetHandler(),
    FixedCounterPlacementTargetSetHandler(),
    FixedPlayerCounterPlacementHandler(),
)


__all__ = [
    "COUNTER_PLACEMENT_HANDLERS",
    "FixedCounterPlacementHandler",
    "FixedCounterPlacementBatchHandler",
    "FixedCounterPlacementSetHandler",
    "FixedCounterPlacementTargetSetHandler",
    "FixedPlayerCounterPlacementHandler",
]
