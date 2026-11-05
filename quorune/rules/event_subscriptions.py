from __future__ import annotations

"""Typed multi-event subscriptions for one triggered semantic program."""

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD,
    FIXED_PUBLIC_STATE_INTERVENING_COVERAGE,
    FixedPublicStateConditionSpec,
)
from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap, freeze_value, thaw_value


EVENT_SUBSCRIPTIONS_FIELD = "event_subscriptions"


class EventSubscriptionError(ValueError):
    """A serialized fixed multi-event subscription is malformed."""


def _public_state_leaves(value: Any):
    if isinstance(value, Mapping):
        if value.get("field") == FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD:
            yield value
        else:
            for child in value.values():
                yield from _public_state_leaves(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _public_state_leaves(child)


@dataclass(frozen=True, slots=True)
class FixedEventSubscription:
    event: str
    condition: FrozenMap | None = None

    def __post_init__(self) -> None:
        if type(self.event) is not str or not self.event:
            raise EventSubscriptionError(
                "Event subscription identities must be nonempty strings"
            )
        if self.condition is not None and isinstance(self.condition, Mapping):
            object.__setattr__(
                self,
                "condition",
                freeze_value(dict(self.condition), field="event condition"),
            )
        elif self.condition is not None:
            raise EventSubscriptionError(
                "Event subscription conditions must be immutable mappings"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "condition": (
                thaw_value(self.condition)
                if self.condition is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixedEventSubscription":
        if set(value) != {"event", "condition"}:
            raise EventSubscriptionError(
                "Event subscription fields must be exactly event and condition"
            )
        condition = value["condition"]
        if condition is not None and not isinstance(condition, Mapping):
            raise EventSubscriptionError(
                "Event subscription condition must be an object or null"
            )
        return cls(
            event=str(value["event"] or ""),
            condition=(
                freeze_value(dict(condition), field="event condition")
                if condition is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class FixedEventSubscriptionSet:
    subscriptions: tuple[FixedEventSubscription, ...]

    def __post_init__(self) -> None:
        if len(self.subscriptions) < 2:
            raise EventSubscriptionError(
                "Multi-event programs require at least two subscriptions"
            )
        events = [subscription.event for subscription in self.subscriptions]
        if len(events) != len(set(events)):
            raise EventSubscriptionError(
                "Multi-event subscriptions must have unique event identities"
            )

    def subscription_for(self, event: str) -> FixedEventSubscription | None:
        return next(
            (
                subscription
                for subscription in self.subscriptions
                if subscription.event == event
            ),
            None,
        )

    def with_shared_condition(self, condition: Mapping[str, Any]) -> FixedEventSubscriptionSet:
        """Conjoin one shared predicate with each event's occurrence filter."""
        return FixedEventSubscriptionSet(tuple(
            FixedEventSubscription(
                subscription.event,
                {"all": [subscription.condition, condition]}
                if subscription.condition is not None else condition,
            )
            for subscription in self.subscriptions
        ))

    def shared_public_state_condition(self) -> Mapping[str, Any] | None:
        """Recover the one intervening predicate shared by every subscription."""
        leaves = tuple(tuple(_public_state_leaves(sub.condition)) for sub in self.subscriptions)
        if not any(leaves):
            return None
        if any(len(conditions) != 1 for conditions in leaves):
            raise EventSubscriptionError("Each conditioned event requires one intervening predicate")
        shared = leaves[0][0]
        if (set(shared) != {"field", "op", "value", "condition"}
                or shared["op"] != "truthy" or shared["value"] is not True):
            raise EventSubscriptionError("The shared intervening predicate is malformed")
        FixedPublicStateConditionSpec.from_dict(shared["condition"])
        for sub in self.subscriptions:
            condition = sub.condition
            if condition == shared:
                continue
            terms = condition["all"] if set(condition) == {"all"} else ()
            if (not isinstance(terms, tuple) or len(terms) != 2
                    or not isinstance(terms[0], Mapping) or terms[1] != shared):
                raise EventSubscriptionError("Conditioned events must conjoin the same intervening predicate")
        return shared

    def to_condition(self) -> dict[str, Any]:
        return {
            EVENT_SUBSCRIPTIONS_FIELD: [
                subscription.to_dict() for subscription in self.subscriptions
            ]
        }

    @classmethod
    def from_condition(
        cls,
        condition: Mapping[str, Any] | None,
    ) -> "FixedEventSubscriptionSet | None":
        if not isinstance(condition, Mapping):
            return None
        if EVENT_SUBSCRIPTIONS_FIELD not in condition:
            return None
        if set(condition) != {EVENT_SUBSCRIPTIONS_FIELD}:
            raise EventSubscriptionError(
                "Multi-event condition must contain only event_subscriptions"
            )
        raw = condition[EVENT_SUBSCRIPTIONS_FIELD]
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise EventSubscriptionError(
                "event_subscriptions must be an array"
            )
        if any(not isinstance(value, Mapping) for value in raw):
            raise EventSubscriptionError(
                "event_subscriptions must contain objects"
            )
        return cls(
            tuple(
                FixedEventSubscription.from_dict(value)
                for value in raw
                if isinstance(value, Mapping)
            )
        )


def fixed_event_subscription_condition(
    subscriptions: Sequence[FixedEventSubscription],
) -> dict[str, Any]:
    """Serialize one compiler-owned fixed subscription set."""

    return FixedEventSubscriptionSet(tuple(subscriptions)).to_condition()


def intervening_recheck_condition(program, *, context, controller):
    """Select the persisted subscription's shared predicate for resolution."""
    condition = program.event_condition
    if (FIXED_PUBLIC_STATE_INTERVENING_COVERAGE not in program.coverage
            or EVENT_SUBSCRIPTIONS_FIELD not in condition):
        return condition
    try:
        subscriptions = FixedEventSubscriptionSet.from_condition(condition)
        assert subscriptions is not None
        shared = subscriptions.shared_public_state_condition()
        if (shared is None or type(controller) is not str
                or context.get('intervening_condition_controller') != controller
                or not any(sub.event.removesuffix('.self') == context.get('event')
                           for sub in subscriptions.subscriptions)):
            raise EventSubscriptionError('Conditioned trigger lost its occurrence or locked controller')
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    return shared


__all__ = [
    "EVENT_SUBSCRIPTIONS_FIELD",
    "EventSubscriptionError",
    "FixedEventSubscription",
    "FixedEventSubscriptionSet",
    "fixed_event_subscription_condition",
    "intervening_recheck_condition",
]
