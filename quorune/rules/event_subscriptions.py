from __future__ import annotations

"""Typed multi-event subscriptions for one triggered semantic program."""

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD,
    FIXED_PUBLIC_STATE_INTERVENING_COVERAGE,
    FixedPublicStateConditionSpec,
)
from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap, freeze_value, thaw_value
from ..scheduled_player import (
    UPKEEP_HAND_COUNT_CAPABILITY, UpkeepParticipantHandCondition,
    is_upkeep_hand_condition, upkeep_hand_condition_holds,
)
from .scheduled_player_shapes import SCHEDULED_PLAYER_CAPABILITY, is_upkeep_participant_schedule


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


def _upkeep_hand_condition_leaf(condition: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    leaves = tuple(_public_state_leaves(condition))
    if not any(is_upkeep_hand_condition(leaf.get('condition')) for leaf in leaves):
        return None
    clauses = condition['all'] if isinstance(condition, Mapping) and set(condition) == {'all'} else ()
    if (len(leaves) != 1 or not isinstance(clauses, (list, tuple)) or len(clauses) != 2
            or clauses[1] != leaves[0] or not is_upkeep_participant_schedule(clauses[0])):
        raise EventSubscriptionError('Upkeep hand conditions require a closed upkeep participant')
    leaf = leaves[0]
    if set(leaf) != {'field', 'op', 'value', 'condition'} or leaf['op'] != 'truthy' or leaf['value'] is not True:
        raise EventSubscriptionError('The upkeep intervening predicate is malformed')
    UpkeepParticipantHandCondition.from_dict(leaf['condition'])
    return leaf


def upkeep_hand_runtime_dependencies(program) -> tuple[str, ...]:
    leaf = _upkeep_hand_condition_leaf(program.event_condition)
    if leaf is None:
        if UPKEEP_HAND_COUNT_CAPABILITY in program.capability_dependencies:
            raise EventSubscriptionError('A declared upkeep hand condition requires its typed predicate')
        return ()
    if (program.event != 'step.begin' or program.active_zone != 'battlefield'
            or not {'intervening_condition', FIXED_PUBLIC_STATE_INTERVENING_COVERAGE} <= set(program.coverage)):
        raise EventSubscriptionError('Upkeep hand conditions require a battlefield intervening upkeep trigger')
    return ('trigger.condition.fixed_public_state', SCHEDULED_PLAYER_CAPABILITY, UPKEEP_HAND_COUNT_CAPABILITY)


def public_state_intervening_actual(
    host,
    condition: Mapping[str, Any],
    *,
    source,
    context: Mapping[str, Any],
) -> bool:
    raw_condition = condition.get("condition")
    if not isinstance(raw_condition, Mapping):
        raise GameRuleError(
            "Public-state intervening conditions require a typed descriptor"
        )
    try:
        if is_upkeep_hand_condition(raw_condition):
            hand_condition = UpkeepParticipantHandCondition.from_dict(raw_condition)
            return upkeep_hand_condition_holds(host.state, context, hand_condition)
        public_condition = FixedPublicStateConditionSpec.from_dict(raw_condition)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    controller = context.get("intervening_condition_controller")
    if controller is not None and (
        type(controller) is not str or controller not in host.state.players
    ):
        raise GameRuleError(
            "Intervening-condition controller must name an active seat"
        )
    public_source = copy.copy(source)
    if isinstance(controller, str):
        public_source.controller = controller
    locked_identity = context.get("source_logical_object_id")
    if (
        isinstance(controller, str)
        and type(locked_identity) is str
        and locked_identity
        and public_source.logical_object_id != locked_identity
    ):
        public_source.zone = "departed"
        public_source.attached_to = None
        public_source.counters = {}
        identity_prefix = f"{public_source.object_id}@"
        incarnation = locked_identity.removeprefix(identity_prefix)
        if (
            not locked_identity.startswith(identity_prefix)
            or not incarnation.isdigit()
        ):
            raise GameRuleError(
                "Intervening-condition source identity is malformed"
            )
        public_source.zone_change_counter = int(incarnation)
    return host._fixed_public_state_condition_holds(
        public_source,
        public_condition,
    )


def intervening_recheck_condition(program, *, context, controller):
    """Select the intervening predicate without reapplying its occurrence filter."""
    condition = program.event_condition
    upkeep = UPKEEP_HAND_COUNT_CAPABILITY in program.capability_dependencies
    if not upkeep and (FIXED_PUBLIC_STATE_INTERVENING_COVERAGE not in program.coverage
                       or EVENT_SUBSCRIPTIONS_FIELD not in condition):
        return condition
    try:
        if upkeep:
            shared = _upkeep_hand_condition_leaf(condition)
            events = (program.event,)
        else:
            subscriptions = FixedEventSubscriptionSet.from_condition(condition)
            assert subscriptions is not None
            shared = subscriptions.shared_public_state_condition()
            events = tuple(sub.event.removesuffix('.self') for sub in subscriptions.subscriptions)
        if (shared is None or type(controller) is not str
                or context.get('intervening_condition_controller') != controller
                or context.get('event') not in events):
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
    "public_state_intervening_actual",
    "upkeep_hand_runtime_dependencies",
]
