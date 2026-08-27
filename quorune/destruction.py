from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

from .counter_state import (
    CounterChange,
    CounterStateError,
    CounterStatePlan,
    apply_counter_changes,
    plan_counter_changes,
    validate_counter_changes,
)
from .keyword_abilities import normalized_effective_keywords
from .regeneration import apply_regeneration_replacement, RegenerationError
from .replacement.immutable import (
    FrozenMap,
    freeze_value,
    ImmutableValueError,
    thaw_value,
)
from .zone_trigger_events import ZoneTransitionKind


class DestructionError(ValueError):
    """A destruction proposal is malformed, unsupported, or stale."""


class DestructionCause(str, Enum):
    EFFECT = "effect"
    STATE_BASED_ACTION = "state_based_action"


class DestructionDisposition(str, Enum):
    DESTROY = "destroy"
    INDESTRUCTIBLE = "indestructible"
    REGENERATION = "regeneration"
    SHIELD_COUNTER = "shield_counter"


class DestructionHost(Protocol):
    state: Any

    def _resolve_object(
        self,
        actor: str,
        ref: str,
        *,
        zones: set[str] | None = None,
    ) -> Any: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _remove_object_from_combat(
        self,
        card: Any,
        *,
        reason: str,
    ) -> bool: ...

    def move_card(
        self,
        object_id: str,
        destination: str,
        *,
        reason: str,
        log: bool,
        semantic_events: bool,
        replacement_selections: Sequence[str | Mapping[str, Any]],
    ) -> Any: ...

    def _move_cards_simultaneously(
        self,
        changes: Sequence[tuple[str, str]],
        *,
        reason: str,
        log: bool = False,
        replacement_selections: Sequence[
            str | None | Mapping[str, Any]
        ] = (),
        transition_kinds: Mapping[str, ZoneTransitionKind] | None = None,
    ) -> list[Any]: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        summary: str,
        details: Mapping[str, Any] | None = None,
        *,
        importance: int = 1,
        changed_objects: Sequence[str] = (),
        changed_players: Sequence[str] = (),
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class DestructionRequest:
    object_id: str
    logical_object_id: str

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value
            for value in (self.object_id, self.logical_object_id)
        ):
            raise DestructionError(
                "Destruction requests require physical and logical identity"
            )


@dataclass(frozen=True, slots=True)
class DestructionEntry:
    object_id: str
    object_ref: str
    logical_object_id: str
    controller: str
    disposition: DestructionDisposition
    indestructible: bool
    shield_counters: int
    regeneration_shields: int

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value
            for value in (
                self.object_id,
                self.object_ref,
                self.logical_object_id,
                self.controller,
            )
        ):
            raise DestructionError(
                "Destruction entries require complete object identity"
            )
        if not isinstance(self.disposition, DestructionDisposition):
            raise DestructionError("Destruction disposition must be typed")
        if type(self.indestructible) is not bool:
            raise DestructionError(
                "Destruction indestructible snapshot must be a boolean"
            )
        if type(self.shield_counters) is not int or self.shield_counters < 0:
            raise DestructionError(
                "Destruction shield snapshot must be a nonnegative integer"
            )
        if (
            type(self.regeneration_shields) is not int
            or self.regeneration_shields < 0
        ):
            raise DestructionError(
                "Regeneration shield snapshot must be a nonnegative integer"
            )


@dataclass(frozen=True, slots=True)
class DestructionPlan:
    cause: DestructionCause
    actor: str | None
    reason: str
    entries: tuple[DestructionEntry, ...]
    shield_counter_plan: CounterStatePlan
    regeneration_prohibited: bool = False
    destruction_event_order: tuple[str, ...] = ()
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.cause, DestructionCause):
            raise DestructionError("Destruction cause must be typed")
        if not isinstance(self.reason, str) or not self.reason:
            raise DestructionError("Destruction requires a nonempty reason")
        if type(self.regeneration_prohibited) is not bool:
            raise DestructionError(
                "Destruction regeneration prohibition must be boolean"
            )
        if self.cause is DestructionCause.EFFECT:
            if not isinstance(self.actor, str) or not self.actor:
                raise DestructionError("Effect destruction requires an actor")
        else:
            if self.actor is not None:
                raise DestructionError("State-based destruction has no actor")
            if self.regeneration_prohibited:
                raise DestructionError(
                    "State-based destruction cannot prohibit regeneration"
                )
        if not isinstance(self.entries, tuple) or any(
            not isinstance(entry, DestructionEntry) for entry in self.entries
        ):
            raise DestructionError("Destruction plans require typed entries")
        object_ids = tuple(entry.object_id for entry in self.entries)
        if object_ids != tuple(sorted(object_ids)):
            raise DestructionError(
                "Destruction plan entries must be canonically ordered"
            )
        if len(object_ids) != len(set(object_ids)):
            raise DestructionError(
                "A permanent may be destroyed only once per batch"
            )
        if any(
            entry.disposition
            is not _destruction_disposition(
                cause=self.cause,
                indestructible=entry.indestructible,
                shield_counters=entry.shield_counters,
                regeneration_shields=entry.regeneration_shields,
                regeneration_prohibited=self.regeneration_prohibited,
            )
            for entry in self.entries
        ):
            raise DestructionError(
                "Destruction entry disposition contradicts its snapshot"
            )
        if self.cause is DestructionCause.EFFECT and not self.regeneration_prohibited and any(
            not entry.indestructible
            and entry.shield_counters
            and entry.regeneration_shields
            for entry in self.entries
        ):
            raise DestructionError(
                "Competing shield-counter and regeneration replacements "
                "require an unsupported affected-player choice"
            )
        if not isinstance(self.shield_counter_plan, CounterStatePlan):
            raise DestructionError(
                "Destruction shield changes require a typed counter plan"
            )
        expected_shields = {
            entry.object_id: entry
            for entry in self.entries
            if entry.disposition is DestructionDisposition.SHIELD_COUNTER
        }
        transitions = self.shield_counter_plan.transitions
        if len(transitions) != len(expected_shields):
            raise DestructionError(
                "Destruction shield plan does not match its entries"
            )
        for transition in transitions:
            entry = expected_shields.get(transition.subject_id)
            if (
                entry is None
                or transition.subject_kind != "permanent"
                or transition.counter_name != "shield"
                or transition.requested_delta != -1
                or transition.applied_delta != -1
                or transition.before != entry.shield_counters
                or transition.after != entry.shield_counters - 1
                or transition.expected_zone != "battlefield"
                or transition.expected_logical_object_id
                != entry.logical_object_id
            ):
                raise DestructionError(
                    "Destruction shield transition is malformed"
                )
        if not isinstance(self.replacement_selections, tuple):
            raise DestructionError(
                "Destruction replacement selections must be immutable"
            )
        order = tuple(self.destruction_event_order)
        if any(type(value) is not str or not value for value in order):
            raise DestructionError(
                "Destruction event order requires object identities"
            )
        destroyed = self.destroyed_object_ids
        if order and (
            len(order) != len(set(order)) or set(order) != set(destroyed)
        ):
            raise DestructionError(
                "Destruction event order must cover each destroyed object once"
            )
        selections = _canonical_selections(self.replacement_selections)
        object.__setattr__(self, "destruction_event_order", order)
        object.__setattr__(self, "replacement_selections", selections)

    @property
    def destroyed_object_ids(self) -> tuple[str, ...]:
        return tuple(
            entry.object_id
            for entry in self.entries
            if entry.disposition is DestructionDisposition.DESTROY
        )


@dataclass(frozen=True, slots=True)
class DestructionResult:
    destroyed_object_ids: tuple[str, ...]
    shielded_object_ids: tuple[str, ...]
    regenerated_object_ids: tuple[str, ...]
    indestructible_object_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        groups = (
            self.destroyed_object_ids,
            self.shielded_object_ids,
            self.regenerated_object_ids,
            self.indestructible_object_ids,
        )
        if any(
            not isinstance(group, tuple)
            or any(not isinstance(value, str) or not value for value in group)
            for group in groups
        ):
            raise DestructionError(
                "Destruction results require immutable object identities"
            )
        combined = tuple(value for group in groups for value in group)
        if len(combined) != len(set(combined)):
            raise DestructionError(
                "Destruction result dispositions must be disjoint"
            )
        if any(group != tuple(sorted(group)) for group in groups):
            raise DestructionError(
                "Destruction result identities must be canonically ordered"
            )


def request_for_card(card: Any) -> DestructionRequest:
    return DestructionRequest(
        object_id=getattr(card, "object_id", None),
        logical_object_id=getattr(card, "logical_object_id", None),
    )


def _effective_keywords(host: DestructionHost, card: Any) -> frozenset[str]:
    try:
        return normalized_effective_keywords(host, card)
    except ValueError as exc:
        raise DestructionError(
            f"Unable to compute effective destruction keywords: {exc}"
        ) from exc


def _destruction_disposition(
    *,
    cause: DestructionCause,
    indestructible: bool,
    shield_counters: int,
    regeneration_shields: int = 0,
    regeneration_prohibited: bool = False,
) -> DestructionDisposition:
    if not isinstance(cause, DestructionCause):
        raise DestructionError("Destruction cause must be typed")
    if type(indestructible) is not bool:
        raise DestructionError(
            "Destruction Indestructible state must be a boolean"
        )
    if type(shield_counters) is not int or shield_counters < 0:
        raise DestructionError(
            "Destruction shield count must be a nonnegative integer"
        )
    if type(regeneration_shields) is not int or regeneration_shields < 0:
        raise DestructionError(
            "Regeneration shield count must be a nonnegative integer"
        )
    if type(regeneration_prohibited) is not bool:
        raise DestructionError(
            "Destruction regeneration prohibition must be boolean"
        )
    if indestructible:
        return DestructionDisposition.INDESTRUCTIBLE
    if regeneration_shields and not regeneration_prohibited:
        return DestructionDisposition.REGENERATION
    if cause is DestructionCause.EFFECT and shield_counters:
        return DestructionDisposition.SHIELD_COUNTER
    return DestructionDisposition.DESTROY


def _canonical_selections(
    values: Sequence[str | Mapping[str, Any]],
) -> tuple[str | FrozenMap, ...]:
    result: list[str | FrozenMap] = []
    for value in values:
        if isinstance(value, str):
            if not value:
                raise DestructionError(
                    "Destruction replacement selections must be nonempty"
                )
            result.append(value)
            continue
        if not isinstance(value, Mapping):
            raise DestructionError(
                "Destruction replacement selections must be strings or objects"
            )
        try:
            frozen = freeze_value(value, field="replacement selection")
        except ImmutableValueError as exc:
            raise DestructionError(str(exc)) from exc
        if not isinstance(frozen, FrozenMap):
            raise DestructionError("Replacement selection did not freeze")
        result.append(frozen)
    return tuple(result)


def prepare_destructions(
    host: DestructionHost,
    requests: Sequence[DestructionRequest],
    *,
    cause: DestructionCause,
    actor: str | None,
    reason: str,
    regeneration_prohibited: bool = False,
    event_order: Sequence[str] = (),
    replacement_selections: Sequence[str | Mapping[str, Any]] = (),
) -> DestructionPlan:
    """Snapshot one simultaneous destruction family before any mutation."""

    if not isinstance(cause, DestructionCause):
        raise DestructionError("Destruction cause must be typed")
    if not isinstance(reason, str) or not reason:
        raise DestructionError("Destruction requires a nonempty reason")
    if type(regeneration_prohibited) is not bool:
        raise DestructionError(
            "Destruction regeneration prohibition must be boolean"
        )
    if cause is DestructionCause.EFFECT and (
        not isinstance(actor, str) or not actor
    ):
        raise DestructionError("Effect destruction requires an actor")
    if cause is DestructionCause.STATE_BASED_ACTION and actor is not None:
        raise DestructionError("State-based destruction has no actor")
    if cause is DestructionCause.STATE_BASED_ACTION and regeneration_prohibited:
        raise DestructionError("State-based destruction cannot prohibit regeneration")

    supplied_requests = tuple(requests)
    if any(
        not isinstance(request, DestructionRequest)
        for request in supplied_requests
    ):
        raise DestructionError("Destruction plans require typed requests")
    canonical_requests = tuple(
        sorted(supplied_requests, key=lambda request: request.object_id)
    )
    object_ids = tuple(request.object_id for request in canonical_requests)
    if len(object_ids) != len(set(object_ids)):
        raise DestructionError("A permanent may be destroyed only once per batch")

    entries: list[DestructionEntry] = []
    shield_changes: list[CounterChange] = []
    for request in canonical_requests:
        card = host.state.cards.get(request.object_id)
        if card is None:
            raise DestructionError("Destruction permanent does not exist")
        if card.zone != "battlefield" or bool(card.phased_out):
            raise DestructionError(
                "Only a phased-in battlefield permanent can be destroyed"
            )
        if card.logical_object_id != request.logical_object_id:
            raise DestructionError(
                "Destruction permanent changed logical identity"
            )
        keywords = _effective_keywords(host, card)
        raw_shield_count = card.counters.get("shield", 0)
        if type(raw_shield_count) is not int or raw_shield_count < 0:
            raise DestructionError(
                "Shield counters must be nonnegative integers"
            )
        shield_count = raw_shield_count
        regeneration_count = getattr(card, "regeneration_shields", None)
        if type(regeneration_count) is not int or regeneration_count < 0:
            raise DestructionError(
                "Regeneration shields must be nonnegative integers"
            )
        indestructible = "indestructible" in keywords
        if (
            cause is DestructionCause.EFFECT
            and not regeneration_prohibited
            and not indestructible
            and shield_count
            and regeneration_count
        ):
            raise DestructionError(
                "Competing shield-counter and regeneration replacements "
                "require an unsupported affected-player choice"
            )
        disposition = _destruction_disposition(
            cause=cause,
            indestructible=indestructible,
            shield_counters=shield_count,
            regeneration_shields=regeneration_count,
            regeneration_prohibited=regeneration_prohibited,
        )
        if disposition is DestructionDisposition.SHIELD_COUNTER:
            shield_changes.append(
                CounterChange(
                    subject_kind="permanent",
                    subject_id=card.object_id,
                    counter_name="shield",
                    amount=-1,
                    expected_zone="battlefield",
                    expected_logical_object_id=card.logical_object_id,
                )
            )
        entries.append(
            DestructionEntry(
                object_id=card.object_id,
                object_ref=card.ref,
                logical_object_id=card.logical_object_id,
                controller=card.controller,
                disposition=disposition,
                indestructible=indestructible,
                shield_counters=shield_count,
                regeneration_shields=regeneration_count,
            )
        )

    if not isinstance(replacement_selections, (list, tuple)):
        raise DestructionError(
            "Destruction replacement selections must be a list"
        )
    selections = _canonical_selections(replacement_selections)
    supplied_order = tuple(event_order)
    if any(type(value) is not str or not value for value in supplied_order):
        raise DestructionError(
            "Destruction event order requires object identities"
        )
    if supplied_order and (
        len(supplied_order) != len(set(supplied_order))
        or set(supplied_order) != set(object_ids)
    ):
        raise DestructionError(
            "Destruction event order must cover each requested object once"
        )
    destroyed_set = {
        entry.object_id
        for entry in entries
        if entry.disposition is DestructionDisposition.DESTROY
    }
    destruction_order = tuple(
        value for value in supplied_order if value in destroyed_set
    )
    try:
        shield_plan = plan_counter_changes(host, shield_changes)
    except CounterStateError as exc:
        raise DestructionError(str(exc)) from exc
    return DestructionPlan(
        cause=cause,
        actor=actor,
        reason=reason,
        entries=tuple(entries),
        shield_counter_plan=shield_plan,
        regeneration_prohibited=regeneration_prohibited,
        destruction_event_order=destruction_order,
        replacement_selections=selections,
    )


def validate_destruction_plan(
    host: DestructionHost,
    plan: DestructionPlan,
) -> None:
    if not isinstance(plan, DestructionPlan):
        raise DestructionError("Destruction commits require a typed plan")
    for entry in plan.entries:
        card = host.state.cards.get(entry.object_id)
        if (
            card is None
            or card.zone != "battlefield"
            or bool(card.phased_out)
            or card.logical_object_id != entry.logical_object_id
        ):
            raise DestructionError("Destruction plan is stale")
        current_indestructible = (
            "indestructible" in _effective_keywords(host, card)
        )
        current_shields = card.counters.get("shield", 0)
        if type(current_shields) is not int or current_shields < 0:
            raise DestructionError("Destruction plan is stale")
        current_regeneration = getattr(card, "regeneration_shields", None)
        if (
            type(current_regeneration) is not int
            or current_regeneration < 0
        ):
            raise DestructionError("Destruction plan is stale")
        current_disposition = _destruction_disposition(
            cause=plan.cause,
            indestructible=current_indestructible,
            shield_counters=current_shields,
            regeneration_shields=current_regeneration,
            regeneration_prohibited=plan.regeneration_prohibited,
        )
        if (
            card.ref != entry.object_ref
            or card.controller != entry.controller
            or current_indestructible != entry.indestructible
            or current_shields != entry.shield_counters
            or current_regeneration != entry.regeneration_shields
            or current_disposition is not entry.disposition
        ):
            raise DestructionError("Destruction plan is stale")
    try:
        validate_counter_changes(host, plan.shield_counter_plan)
    except CounterStateError as exc:
        raise DestructionError(str(exc)) from exc


def _canonical_companion_changes(
    plan: DestructionPlan,
    changes: Sequence[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    seen: dict[str, str] = {}
    for change in changes:
        if (
            not isinstance(change, tuple)
            or len(change) != 2
            or not all(isinstance(value, str) and value for value in change)
        ):
            raise DestructionError(
                "Companion zone changes require object and destination"
            )
        object_id, destination = change
        previous = seen.get(object_id)
        if previous is not None:
            if previous != destination:
                raise DestructionError(
                    "Companion zone changes conflict for one object"
                )
            continue
        seen[object_id] = destination
        result.append(change)
    destroyed = set(plan.destroyed_object_ids)
    if destroyed.intersection(seen):
        raise DestructionError(
            "A destruction move cannot also be a companion zone change"
        )
    return tuple(result)


def commit_destruction_plan(
    host: DestructionHost,
    plan: DestructionPlan,
    *,
    companion_changes: Sequence[tuple[str, str]] = (),
    companion_transition_kinds: Mapping[str, ZoneTransitionKind] | None = None,
) -> DestructionResult:
    """Commit one preflighted destruction family through canonical owners."""

    validate_destruction_plan(host, plan)
    destroyed = plan.destroyed_object_ids
    event_order = plan.destruction_event_order or destroyed
    companions = _canonical_companion_changes(plan, companion_changes)
    transition_kinds = dict(companion_transition_kinds or {})
    if (
        not set(transition_kinds).issubset(
            {object_id for object_id, _destination in companions}
        )
        or any(
            not isinstance(value, ZoneTransitionKind)
            for value in transition_kinds.values()
        )
    ):
        raise DestructionError(
            "Companion transition kinds must be typed and name companion objects"
        )
    changes = (
        tuple((object_id, "graveyard") for object_id in event_order)
        + companions
    )
    if plan.replacement_selections and companions:
        raise DestructionError(
            "Replacement selections do not support a compound move batch"
        )
    if changes:
        host._move_cards_simultaneously(
            changes,
            reason=plan.reason,
            log=False,
            replacement_selections=tuple(
                thaw_value(value) for value in plan.replacement_selections
            ),
            transition_kinds=transition_kinds,
        )
    apply_counter_changes(host, plan.shield_counter_plan)

    shielded: list[str] = []
    regenerated: list[str] = []
    indestructible: list[str] = []
    for entry in plan.entries:
        if entry.disposition is DestructionDisposition.DESTROY:
            card = host.state.cards[entry.object_id]
            host._log(
                plan.actor,
                "permanent.destroyed",
                f"{entry.object_ref} was destroyed.",
                {
                    "object": entry.object_ref,
                    "cause": plan.cause.value,
                    "destination": card.zone,
                    "reason": plan.reason,
                },
                importance=2,
                changed_objects=[entry.object_id],
                changed_players=[entry.controller],
            )
        elif entry.disposition is DestructionDisposition.SHIELD_COUNTER:
            shielded.append(entry.object_id)
            host._log(
                plan.actor,
                "permanent.destroy.replaced",
                f"A shield counter replaced destruction of {entry.object_ref}.",
                {
                    "object": entry.object_ref,
                    "replacement": "shield_counter",
                    "reason": plan.reason,
                },
                importance=2,
                changed_objects=[entry.object_id],
                changed_players=[entry.controller],
            )
        elif entry.disposition is DestructionDisposition.REGENERATION:
            regenerated.append(entry.object_id)
            try:
                apply_regeneration_replacement(
                    host,
                    entry.object_id,
                    actor=plan.actor,
                    reason=plan.reason,
                    logical_object_id=entry.logical_object_id,
                    expected_shields=entry.regeneration_shields,
                )
            except RegenerationError as exc:
                raise DestructionError(str(exc)) from exc
            host._log(
                plan.actor,
                "permanent.destroy.regenerated",
                f"A regeneration shield replaced destruction of {entry.object_ref}.",
                {
                    "object": entry.object_ref,
                    "replacement": "regeneration",
                    "reason": plan.reason,
                },
                importance=2,
                changed_objects=[entry.object_id],
                changed_players=[entry.controller],
            )
        else:
            indestructible.append(entry.object_id)
            host._log(
                plan.actor,
                "permanent.destroy.prohibited",
                f"{entry.object_ref} could not be destroyed.",
                {
                    "object": entry.object_ref,
                    "prohibition": "indestructible",
                    "reason": plan.reason,
                },
                importance=1,
                changed_objects=[entry.object_id],
                changed_players=[entry.controller],
            )
    return DestructionResult(
        destroyed_object_ids=destroyed,
        shielded_object_ids=tuple(shielded),
        regenerated_object_ids=tuple(regenerated),
        indestructible_object_ids=tuple(indestructible),
    )


def destroy_permanent_refs(
    host: DestructionHost,
    object_refs: Sequence[str],
    *,
    actor: str,
    reason: str,
    regeneration_prohibited: bool = False,
    replacement_selections: Sequence[str | Mapping[str, Any]] = (),
) -> DestructionResult:
    cards = tuple(
        host._resolve_object(actor, ref, zones={"battlefield"})
        for ref in object_refs
    )
    return commit_destruction_plan(
        host,
        prepare_destructions(
            host,
            tuple(request_for_card(card) for card in cards),
            cause=DestructionCause.EFFECT,
            actor=actor,
            reason=reason,
            regeneration_prohibited=regeneration_prohibited,
            replacement_selections=replacement_selections,
        ),
    )


__all__ = [
    "DestructionCause",
    "DestructionDisposition",
    "DestructionEntry",
    "DestructionError",
    "DestructionPlan",
    "DestructionRequest",
    "DestructionResult",
    "commit_destruction_plan",
    "destroy_permanent_refs",
    "prepare_destructions",
    "request_for_card",
    "validate_destruction_plan",
]
