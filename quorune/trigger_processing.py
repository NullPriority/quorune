from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .ability_fragments import canonical_ability_fragments
from .delayed_triggers import materialize_delayed_trigger
from .errors import GameRuleError, StateInvariantError
from .model import CardInstance, DelayedTrigger, StackItem
from .trigger_participation import WardSpec
from .trigger_targeting import begin_pending_trigger_target_selection
from .trigger_batches import (
    PendingTriggerItem,
    TriggerBatchError,
    begin_pending_trigger_placement,
    complete_pending_trigger_group,
    create_pending_trigger_batch,
    merge_pending_trigger_batch,
)


class TriggerProcessingHost(Protocol):
    """Narrow authoritative services around the pure CR 603.3 owner."""

    state: Any
    active_seats: Sequence[str]
    seats: Sequence[str]
    permissions: Any

    def apnap_order(self) -> Sequence[str]: ...

    def _dispatch_semantic_event(
        self,
        event_kind: str,
        context: Mapping[str, Any],
        *,
        trigger_batch: list[StackItem],
    ) -> Any: ...

    def _semantic_pause_annotation(self) -> Any: ...

    def _next_ref(self, prefix: str) -> str: ...

    def _stable_runtime_id(self, kind: str, ref: str) -> str: ...

    def _grant_priority(self, seat: str | None) -> None: ...

    def _effective_card_data(
        self, card: str | CardInstance
    ) -> Mapping[str, Any]: ...

    def _resolve_object(
        self, actor: str, ref: str, *, zones: set[str]
    ) -> CardInstance: ...

    def display_name(self, object_id: str) -> str: ...

    def _log(self, *args: Any, **kwargs: Any) -> None: ...


class TriggerProcessingOwner:
    """Authoritative owner for represented CR 603 trigger transitions."""

    def __init__(self, host: TriggerProcessingHost) -> None:
        self.host = host

    @property
    def state(self) -> Any:
        """Expose the authoritative state path to structural ownership guards."""

        return self.host.state

    def schedule_delayed_trigger(
        self,
        *,
        controller: str,
        label: str,
        event_kind: str,
        condition: Mapping[str, Any],
        stack_template: Mapping[str, Any],
        source_object_id: str | None = None,
        referred_object_ids: Sequence[str] = (),
        once: bool = True,
        expires_turn_sequence: int | None = None,
    ) -> DelayedTrigger:
        ref = self.host._next_ref("DT")
        source = (
            self.state.cards.get(source_object_id)
            if source_object_id is not None
            else None
        )
        trigger = DelayedTrigger(
            trigger_id=self.host._stable_runtime_id("delayed-trigger", ref),
            ref=ref,
            controller=controller,
            label=label,
            source_object_id=source_object_id,
            source_logical_object_id=(
                source.logical_object_id if source is not None else None
            ),
            event_kind=event_kind,
            condition=dict(condition),
            stack_template=dict(stack_template),
            once=once,
            created_turn_sequence=self.state.turn_sequence,
            expires_turn_sequence=expires_turn_sequence,
            referred_object_ids=list(referred_object_ids),
        )
        self.state.delayed_triggers.append(trigger)
        self.host._log(
            controller,
            "trigger.delayed.created",
            f"Created delayed trigger {trigger.ref}: {label}.",
            {"trigger": trigger.ref, "condition": dict(condition)},
            importance=1,
        )
        return trigger

    def trigger_matches(
        self,
        trigger: DelayedTrigger,
        event_kind: str,
        context: Mapping[str, Any],
    ) -> bool:
        if not trigger.active or trigger.event_kind != event_kind:
            return False
        if (
            trigger.expires_turn_sequence is not None
            and self.state.turn_sequence > trigger.expires_turn_sequence
        ):
            trigger.active = False
            return False
        for key, original_expected in trigger.condition.items():
            expected = original_expected
            if key == "after_turn_sequence":
                if self.host.state.turn_sequence <= int(expected):
                    return False
                continue
            if key == "next_turn_after_sequence":
                expected_turn_sequence = int(expected) + 1
                if self.host.state.turn_sequence > expected_turn_sequence:
                    trigger.active = False
                    return False
                if self.host.state.turn_sequence != expected_turn_sequence:
                    return False
                continue
            if key == "player" and expected in {"controller", "$controller"}:
                expected = trigger.controller
            if isinstance(expected, (list, tuple, set)):
                if context.get(key) not in expected:
                    return False
            elif context.get(key) != expected:
                return False
        return True

    def matching_delayed_triggers(
        self,
        event_kind: str,
        context: Mapping[str, Any],
    ) -> list[DelayedTrigger]:
        matches = [
            trigger
        for trigger in self.state.delayed_triggers
            if self.trigger_matches(trigger, event_kind, context)
        ]
        for trigger in matches:
            if trigger.once:
                trigger.active = False
        return matches

    def materialize_delayed_trigger(
        self, trigger: DelayedTrigger
    ) -> StackItem:
        ref = self.host._next_ref("S")
        return materialize_delayed_trigger(
            trigger,
            ref=ref,
            stack_id=self.host._stable_runtime_id("stack", ref),
            visibility=self.host.seats,
        )

    def issue_trigger_order(
        self,
        controller: str,
        options: Sequence[tuple[str, str]],
        continuation: Mapping[str, Any],
    ) -> None:
        self.host.permissions.issue(
            kind="trigger.order",
            role="pilot",
            actors=[controller],
            allowed_actions=["order"],
            payload_by_actor={
                controller: {
                    "triggers": [
                        {"id": ref, "label": label}
                        for ref, label in options
                    ],
                    "instruction": "Order bottom-to-top on the stack.",
                }
            },
            continuation=dict(continuation),
        )

    def complete_trigger_order_decision(self, decision: Any) -> None:
        controller = decision.actors[0]
        response = decision.responses[controller]
        values = list(response.get("triggers") or response.get("order") or [])
        complete_trigger_order(
            self.host,
            controller=controller,
            values=values,
            continuation=decision.continuation,
        )

    def collect_ward_occurrences(self, targeted_item: StackItem) -> list[str]:
        """Collect current typed Ward abilities into the ordinary batch."""

        occurrences: list[StackItem] = []
        seen: set[str] = set()
        for target_ref in targeted_item.targets:
            normalized_target_ref = str(target_ref)
            if normalized_target_ref in seen:
                continue
            seen.add(normalized_target_ref)
            try:
                permanent = self.host._resolve_object(
                    targeted_item.controller,
                    normalized_target_ref,
                    zones={"battlefield"},
                )
            except GameRuleError:
                continue
            if permanent.controller == targeted_item.controller:
                continue
            fragments = canonical_ability_fragments(
                self.host._effective_card_data(permanent).get(
                    "ability_fragments", ()
                )
            )
            for ward_spec in fragments:
                if not isinstance(ward_spec, WardSpec):
                    continue
                ref = self.host._next_ref("S")
                occurrences.append(
                    StackItem(
                        stack_id=self.host._stable_runtime_id("stack", ref),
                        ref=ref,
                        kind="triggered_ability",
                        controller=permanent.controller,
                        label=(
                            f"{self.host.display_name(permanent.object_id)} — Ward"
                        ),
                        source_object_id=permanent.object_id,
                        semantic_key="builtin:ward",
                        visibility=list(self.host.seats),
                        context={
                            "event": "object.became_target",
                            "source_logical_object_id": (
                                permanent.logical_object_id
                            ),
                            "source_zone": "battlefield",
                            "target_stack": targeted_item.ref,
                            "payer": targeted_item.controller,
                            "cost": {"GENERIC": ward_spec.generic_cost},
                            "targeted_permanent": permanent.ref,
                            "ward_spec": ward_spec.to_dict(),
                        },
                    )
                )
        enqueue_trigger_batch(self.host, occurrences)
        return [item.ref for item in occurrences]

    def begin_target_selection(self) -> bool:
        return begin_pending_trigger_target_selection(
            self.host,
            decision_role="pilot",
            log_reason_field="reason",
        )

    def clear_pending_batches(self) -> None:
        self.state.pending_trigger_batches.clear()


class TriggerProcessingHostMixin:
    """Compatibility surface whose implementation authority remains the owner."""

    def schedule_delayed_trigger(self, **kwargs: Any) -> DelayedTrigger:
        return schedule_delayed_trigger(self, **kwargs)  # type: ignore[arg-type]

    def _matching_delayed_triggers(
        self,
        event_kind: str,
        context: Mapping[str, Any],
    ) -> list[DelayedTrigger]:
        """Retain the historical engine adapter for Game Record v3 callers."""

        return matching_delayed_triggers(
            self,  # type: ignore[arg-type]
            event_kind,
            context,
        )

    def _start_trigger_batch(
        self,
        triggers: Sequence[DelayedTrigger],
        *,
        after: str,
    ) -> None:
        """Retain the historical engine adapter for delayed-trigger batches."""

        start_delayed_trigger_batch(
            self,  # type: ignore[arg-type]
            triggers,
            after=after,
        )

    def _delayed_trigger_stack_item(
        self,
        trigger: DelayedTrigger,
    ) -> StackItem:
        """Retain the historical materialization adapter without ownership."""

        return TriggerProcessingOwner(
            self  # type: ignore[arg-type]
        ).materialize_delayed_trigger(trigger)

    def _begin_pending_trigger_target_selection(self) -> bool:
        """Retain the historical target-selection adapter for exact fixtures."""

        return begin_trigger_target_selection(
            self  # type: ignore[arg-type]
        )

    def _complete_trigger_order(self, decision: Any) -> None:
        """Retain the historical Game Record v3/private test adapter."""

        complete_trigger_order_decision(
            self, decision  # type: ignore[arg-type]
        )


def _intrinsic_trigger_items(
    host: TriggerProcessingHost,
    event_kind: str,
    context: Mapping[str, Any],
) -> tuple[StackItem, ...]:
    if event_kind != "step.begin" or context.get("step") != "upkeep":
        return ()
    from .suspend import suspend_upkeep_trigger_items

    return suspend_upkeep_trigger_items(
        host,
        active_player=str(context.get("player") or ""),
    )


def collect_trigger_items(
    host: TriggerProcessingHost,
    event_kind: str,
    context: Mapping[str, Any],
    *,
    held_triggers: Sequence[StackItem] = (),
) -> list[StackItem]:
    """Discover represented abilities into one ordinary occurrence type."""

    triggered = list(held_triggers)
    host._dispatch_semantic_event(
        event_kind,
        context,
        trigger_batch=triggered,
    )
    if host._semantic_pause_annotation() is not None:
        return triggered
    triggered.extend(_intrinsic_trigger_items(host, event_kind, context))
    owner = TriggerProcessingOwner(host)
    triggered.extend(
        owner.materialize_delayed_trigger(trigger)
        for trigger in owner.matching_delayed_triggers(event_kind, context)
    )
    return triggered


def enqueue_trigger_batch(
    host: TriggerProcessingHost,
    items: Sequence[StackItem],
) -> None:
    """Merge already-detected occurrences until CR 603.3 placement starts."""

    if not items:
        return
    pending_items = [
        PendingTriggerItem.from_dict(item.to_dict())
        for item in items
        if item.controller in host.active_seats
    ]
    if not pending_items:
        return
    if host.state.pending_trigger_batches:
        pending = host.state.pending_trigger_batches[-1]
        try:
            merged = merge_pending_trigger_batch(
                pending,
                pending_items,
                apnap_order=host.apnap_order(),
                priority_epoch=host.state.priority_epoch,
            )
        except TriggerBatchError as exc:
            raise StateInvariantError(str(exc)) from exc
        if merged is not None:
            host.state.pending_trigger_batches[-1] = merged
            return
    batch_ref = host._next_ref("TB")
    try:
        batch = create_pending_trigger_batch(
            batch_id=host._stable_runtime_id("trigger-batch", batch_ref),
            ref=batch_ref,
            items=pending_items,
            apnap_order=host.apnap_order(),
            turn_sequence=host.state.turn_sequence,
            priority_epoch=host.state.priority_epoch,
        )
    except TriggerBatchError as exc:
        raise StateInvariantError(str(exc)) from exc
    host.state.pending_trigger_batches.append(batch)


def place_trigger_items(
    host: TriggerProcessingHost,
    values: Sequence[PendingTriggerItem | Mapping[str, Any]],
) -> None:
    """Append validated ordinary triggered abilities to the public stack."""

    for value in values:
        payload = (
            value.to_dict()
            if isinstance(value, PendingTriggerItem)
            else copy.deepcopy(dict(value))
        )
        item = StackItem.from_dict(payload)
        host.state.stack.append(item)
        source = (
            host.state.cards.get(item.source_object_id)
            if item.source_object_id
            else None
        )
        host._log(
            item.controller,
            "stack.trigger",
            f"Queued {item.ref}: {item.label}.",
            {
                "stack": item.ref,
                "source": source.ref if source else None,
                "semantic_program": item.semantic_key,
                "event": item.context.get("event"),
                "trigger": item.context.get("delayed_trigger_ref"),
            },
            importance=2,
            changed_objects=(
                [source.object_id] if source is not None else []
            ),
        )


def begin_pending_trigger_batch(host: TriggerProcessingHost) -> bool:
    """Place waiting groups or issue one same-controller order decision."""

    while host.state.pending_trigger_batches:
        batch = host.state.pending_trigger_batches[0]
        try:
            started = begin_pending_trigger_placement(
                batch,
                apnap_order=host.apnap_order(),
            )
        except TriggerBatchError as exc:
            raise StateInvariantError(str(exc)) from exc
        if started is None:
            host.state.pending_trigger_batches.pop(0)
            continue
        if started is not batch:
            host.state.pending_trigger_batches[0] = started
        batch = started
        group = batch.groups[0]
        if len(group.items) > 1:
            TriggerProcessingOwner(host).issue_trigger_order(
                group.controller,
                [(item.ref, item.label) for item in group.items],
                {
                    "trigger_batch_id": batch.batch_id,
                    "trigger_refs": [item.ref for item in group.items],
                },
            )
            return True
        try:
            ordered, remaining = complete_pending_trigger_group(
                batch,
                controller=group.controller,
                refs=[group.items[0].ref],
            )
        except TriggerBatchError as exc:
            raise StateInvariantError(str(exc)) from exc
        place_trigger_items(host, ordered)
        if remaining is None:
            host.state.pending_trigger_batches.pop(0)
        else:
            host.state.pending_trigger_batches[0] = remaining
    return False


def start_delayed_trigger_batch(
    host: TriggerProcessingHost,
    triggers: Sequence[DelayedTrigger],
    *,
    after: str,
) -> None:
    """Compatibility entry point for callers holding delayed records."""

    if after != "grant_priority":
        raise GameRuleError(
            "The generic trigger batch supports only priority placement"
        )
    enqueue_trigger_batch(
        host,
        [
            TriggerProcessingOwner(host).materialize_delayed_trigger(trigger)
            for trigger in triggers
        ],
    )
    if begin_pending_trigger_batch(host):
        return
    host._grant_priority(host.state.active_player)


def complete_trigger_order(
    host: TriggerProcessingHost,
    *,
    controller: str,
    values: Sequence[Any],
    continuation: Mapping[str, Any],
) -> None:
    """Complete a current or explicitly compatible historical order frame."""

    batch_id = continuation.get("trigger_batch_id") or continuation.get(
        "semantic_trigger_batch_id"
    )
    if batch_id is None and "trigger_ids" in continuation:
        _complete_legacy_delayed_trigger_order(
            host,
            controller=controller,
            values=values,
            continuation=continuation,
        )
        return
    if type(batch_id) is not str or not batch_id:
        raise GameRuleError("Trigger-order continuation is malformed")
    batch_key = (
        "trigger_batch_id"
        if "trigger_batch_id" in continuation
        else "semantic_trigger_batch_id"
    )
    if set(continuation) != {batch_key, "trigger_refs"}:
        raise GameRuleError("Trigger-order continuation is malformed")
    continuation_refs = continuation.get("trigger_refs")
    if (
        not isinstance(continuation_refs, list)
        or not continuation_refs
        or any(
            type(value) is not str or not value
            for value in continuation_refs
        )
        or len(set(continuation_refs)) != len(continuation_refs)
    ):
        raise GameRuleError("Trigger-order continuation is malformed")
    batch_index = next(
        (
            index
            for index, batch in enumerate(host.state.pending_trigger_batches)
            if batch.batch_id == batch_id
        ),
        None,
    )
    if batch_index is None:
        raise GameRuleError("Trigger batch is no longer pending")
    batch = host.state.pending_trigger_batches[batch_index]
    if (
        not batch.groups
        or sorted(continuation_refs)
        != sorted(item.ref for item in batch.groups[0].items)
    ):
        raise GameRuleError("Trigger-order continuation is stale")
    try:
        ordered, remaining = complete_pending_trigger_group(
            batch,
            controller=controller,
            refs=values,
        )
    except TriggerBatchError as exc:
        raise GameRuleError(str(exc)) from exc
    place_trigger_items(host, ordered)
    if remaining is None:
        host.state.pending_trigger_batches.pop(batch_index)
    else:
        host.state.pending_trigger_batches[batch_index] = remaining
    if begin_pending_trigger_batch(host):
        return
    host._grant_priority(host.state.active_player)


def _complete_legacy_delayed_trigger_order(
    host: TriggerProcessingHost,
    *,
    controller: str,
    values: Sequence[Any],
    continuation: Mapping[str, Any],
) -> None:
    if set(continuation) != {"groups", "after", "trigger_ids"} or (
        continuation.get("after") != "grant_priority"
    ):
        raise GameRuleError(
            "Historical delayed-trigger continuation is malformed"
        )
    raw_ids = continuation.get("trigger_ids")
    if not isinstance(raw_ids, list):
        raise GameRuleError(
            "Historical delayed-trigger continuation is malformed"
        )
    ids = list(raw_ids)
    if (
        not ids
        or len(set(ids)) != len(ids)
        or any(type(value) is not str or not value for value in ids)
    ):
        raise GameRuleError(
            "Historical delayed-trigger continuation is malformed"
        )
    available = {
        trigger.trigger_id: trigger
        for trigger in host.state.delayed_triggers
        if trigger.trigger_id in ids
    }
    if any(trigger.controller != controller for trigger in available.values()):
        raise GameRuleError("Only the trigger controller may order this group")
    by_ref = {
        trigger.ref: trigger_id for trigger_id, trigger in available.items()
    }
    resolved = [by_ref.get(str(value), str(value)) for value in values]
    if sorted(resolved) != sorted(ids) or len(available) != len(ids):
        raise GameRuleError(
            "Trigger order must contain every listed trigger exactly once"
        )
    groups = continuation.get("groups", [])
    if not isinstance(groups, list):
        raise GameRuleError("Historical delayed-trigger groups are malformed")
    _validate_legacy_delayed_trigger_groups(
        host,
        groups,
        already_seen=ids,
    )
    place_trigger_items(
        host,
        [
            PendingTriggerItem.from_dict(
                TriggerProcessingOwner(host)
                .materialize_delayed_trigger(available[trigger_id])
                .to_dict()
            )
            for trigger_id in resolved
        ],
    )
    _resume_legacy_delayed_trigger_groups(host, groups)


def _validate_legacy_delayed_trigger_groups(
    host: TriggerProcessingHost,
    groups: Sequence[Any],
    *,
    already_seen: Sequence[str],
) -> None:
    seen = set(already_seen)
    for raw_group in groups:
        if not isinstance(raw_group, Mapping) or set(raw_group) != {
            "controller",
            "trigger_ids",
        }:
            raise GameRuleError("Historical delayed-trigger group is malformed")
        controller = raw_group["controller"]
        ids = raw_group["trigger_ids"]
        if (
            type(controller) is not str
            or not controller
            or not isinstance(ids, list)
            or not ids
            or any(type(value) is not str or not value for value in ids)
            or len(set(ids)) != len(ids)
            or seen.intersection(ids)
        ):
            raise GameRuleError("Historical delayed-trigger group is malformed")
        triggers = {
            trigger.trigger_id: trigger
            for trigger in host.state.delayed_triggers
            if trigger.trigger_id in ids
        }
        if len(triggers) != len(ids) or any(
            trigger.controller != controller for trigger in triggers.values()
        ):
            raise GameRuleError(
                "Historical delayed trigger is no longer available"
            )
        seen.update(ids)


def _resume_legacy_delayed_trigger_groups(
    host: TriggerProcessingHost,
    groups: Sequence[Any],
) -> None:
    remaining = list(groups)
    while remaining:
        raw_group = remaining.pop(0)
        controller = raw_group["controller"]
        ids = raw_group["trigger_ids"]
        triggers = [
            next(
                trigger
                for trigger in host.state.delayed_triggers
                if trigger.trigger_id == trigger_id
            )
            for trigger_id in ids
        ]
        if len(triggers) > 1:
            TriggerProcessingOwner(host).issue_trigger_order(
                controller,
                [(trigger.ref, trigger.label) for trigger in triggers],
                {
                    "groups": remaining,
                    "after": "grant_priority",
                    "trigger_ids": ids,
                },
            )
            return
        place_trigger_items(
            host,
            [
                PendingTriggerItem.from_dict(
                    TriggerProcessingOwner(host)
                    .materialize_delayed_trigger(triggers[0])
                    .to_dict()
                )
            ],
        )
    host._grant_priority(host.state.active_player)


def schedule_delayed_trigger(
    host: TriggerProcessingHost,
    **kwargs: Any,
) -> DelayedTrigger:
    return TriggerProcessingOwner(host).schedule_delayed_trigger(**kwargs)


def matching_delayed_triggers(
    host: TriggerProcessingHost,
    event_kind: str,
    context: Mapping[str, Any],
) -> list[DelayedTrigger]:
    return TriggerProcessingOwner(host).matching_delayed_triggers(
        event_kind, context
    )


def collect_ward_occurrences(
    host: TriggerProcessingHost,
    targeted_item: StackItem,
) -> list[str]:
    return TriggerProcessingOwner(host).collect_ward_occurrences(targeted_item)


def complete_trigger_order_decision(
    host: TriggerProcessingHost,
    decision: Any,
) -> None:
    TriggerProcessingOwner(host).complete_trigger_order_decision(decision)


def begin_trigger_target_selection(host: TriggerProcessingHost) -> bool:
    return TriggerProcessingOwner(host).begin_target_selection()


def clear_pending_trigger_batches(host: TriggerProcessingHost) -> None:
    TriggerProcessingOwner(host).clear_pending_batches()


__all__ = [
    "TriggerProcessingOwner",
    "TriggerProcessingHostMixin",
    "TriggerProcessingHost",
    "begin_pending_trigger_batch",
    "begin_trigger_target_selection",
    "clear_pending_trigger_batches",
    "collect_ward_occurrences",
    "collect_trigger_items",
    "complete_trigger_order",
    "complete_trigger_order_decision",
    "enqueue_trigger_batch",
    "matching_delayed_triggers",
    "place_trigger_items",
    "schedule_delayed_trigger",
    "start_delayed_trigger_batch",
]
