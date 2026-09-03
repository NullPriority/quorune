from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Iterable, Mapping, Sequence

from .immutable import FrozenMap, ImmutableValueError, thaw_value
from .operations import (
    ReplacementOperation,
    ReplacementOperationError,
    lower_operation,
    operation_to_dict,
)


class ReplacementEffectError(ValueError):
    pass


def _translate_error(exc: Exception) -> ReplacementEffectError:
    return ReplacementEffectError(str(exc))


def exact_fields(
    value: Mapping[str, Any], expected: set[str], *, field_name: str
) -> None:
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    details: list[str] = []
    if missing:
        details.append("missing " + ", ".join(missing))
    if unknown:
        details.append("unknown " + ", ".join(unknown))
    raise ReplacementEffectError(
        f"Replacement {field_name} fields: {'; '.join(details)}"
    )


def sequence(value: Any, *, field_name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ReplacementEffectError(
            f"Replacement {field_name} must be an array"
        )
    return value


def mapping_sequence(
    value: Any, *, field_name: str
) -> tuple[Mapping[str, Any], ...]:
    items = sequence(value, field_name=field_name)
    if any(not isinstance(item, Mapping) for item in items):
        raise ReplacementEffectError(
            f"Replacement {field_name} must contain only objects"
        )
    return tuple(items)


def string_sequence(value: Any, *, field_name: str) -> tuple[str, ...]:
    items = sequence(value, field_name=field_name)
    if any(not isinstance(item, str) or not item for item in items):
        raise ReplacementEffectError(
            f"Replacement {field_name} must contain nonempty strings"
        )
    result = tuple(items)
    if len(result) != len(set(result)):
        raise ReplacementEffectError(
            f"Replacement {field_name} must contain unique values"
        )
    return result


class ReplacementClass(IntEnum):
    SELF_REPLACEMENT = 1
    ENTERS_CONTROL = 2
    ENTERS_COPY = 3
    ENTERS_BACK_FACE = 4
    OTHER = 5


@dataclass(frozen=True, slots=True)
class AffectedObject:
    object_id: str
    owner: str
    controller: str | None = None

    def __post_init__(self) -> None:
        if not self.object_id or not self.owner:
            raise ReplacementEffectError(
                "An affected object requires stable object and owner IDs"
            )
        if self.controller == "":
            raise ReplacementEffectError(
                "An affected object controller cannot be empty"
            )

    @property
    def chooser(self) -> str:
        return self.controller or self.owner

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "owner": self.owner,
            "controller": self.controller,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AffectedObject":
        exact_fields(
            value,
            {"object_id", "owner", "controller"},
            field_name="affected_object",
        )
        return cls(
            object_id=str(value.get("object_id") or ""),
            owner=str(value.get("owner") or ""),
            controller=(
                str(value["controller"])
                if value.get("controller") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class EntryReplacementScope:
    entering_objects: tuple[str, ...]
    entering_from_library: tuple[str, ...] = ()
    reserved_zone_changes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "entering_objects",
            "entering_from_library",
            "reserved_zone_changes",
        ):
            values = tuple(str(value) for value in getattr(self, name))
            object.__setattr__(self, name, values)
            if any(not value for value in values) or len(values) != len(
                set(values)
            ):
                raise ReplacementEffectError(
                    f"Entry replacement {name} must be unique stable IDs"
                )
        if not set(self.entering_from_library).issubset(
            self.entering_objects
        ):
            raise ReplacementEffectError(
                "Library entrants must also be entering objects"
            )
        if set(self.reserved_zone_changes).intersection(
            self.entering_objects
        ):
            raise ReplacementEffectError(
                "Entering objects cannot be reserved for another zone change"
            )

    def eligible_zone_choices(
        self, candidates: Iterable[str]
    ) -> tuple[str, ...]:
        unavailable = {*self.entering_objects, *self.reserved_zone_changes}
        return tuple(value for value in candidates if value not in unavailable)

    def reserve_zone_changes(
        self, object_ids: Iterable[str]
    ) -> "EntryReplacementScope":
        selected = tuple(str(value) for value in object_ids)
        if any(not value for value in selected) or len(selected) != len(
            set(selected)
        ):
            raise ReplacementEffectError(
                "Entry replacement zone-change choices must be unique IDs"
            )
        eligible = set(self.eligible_zone_choices(selected))
        invalid = [value for value in selected if value not in eligible]
        if invalid:
            raise ReplacementEffectError(
                "Entry replacement object(s) are not eligible for another "
                "zone change: " + ", ".join(invalid)
            )
        return EntryReplacementScope(
            entering_objects=self.entering_objects,
            entering_from_library=self.entering_from_library,
            reserved_zone_changes=(*self.reserved_zone_changes, *selected),
        )

    def library_order_for_replacement(
        self, library_order: Iterable[str]
    ) -> tuple[str, ...]:
        entering = set(self.entering_from_library)
        return tuple(value for value in library_order if value not in entering)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entering_objects": list(self.entering_objects),
            "entering_from_library": list(self.entering_from_library),
            "reserved_zone_changes": list(self.reserved_zone_changes),
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "EntryReplacementScope":
        exact_fields(
            value,
            {
                "entering_objects",
                "entering_from_library",
                "reserved_zone_changes",
            },
            field_name="entry_scope",
        )
        return cls(
            entering_objects=string_sequence(
                value["entering_objects"], field_name="entering_objects"
            ),
            entering_from_library=string_sequence(
                value["entering_from_library"],
                field_name="entering_from_library",
            ),
            reserved_zone_changes=string_sequence(
                value["reserved_zone_changes"],
                field_name="reserved_zone_changes",
            ),
        )


@dataclass(frozen=True, slots=True)
class ReplaceableEvent:
    event_id: str
    kind: str
    affected_player: str | None
    payload: Mapping[str, Any]
    applied_effects: tuple[str, ...] = ()
    affected_object: AffectedObject | None = None
    children: tuple["ReplaceableEvent", ...] = ()
    entry_scope: EntryReplacementScope | None = None

    def __post_init__(self) -> None:
        if not self.event_id or not self.kind:
            raise ReplacementEffectError(
                "Replaceable events require stable IDs and kinds"
            )
        if (self.affected_player is None) == (self.affected_object is None):
            raise ReplacementEffectError(
                "A replaceable event requires exactly one affected subject"
            )
        if self.affected_player == "":
            raise ReplacementEffectError(
                "An affected player must have a stable seat ID"
            )
        try:
            object.__setattr__(self, "payload", FrozenMap(self.payload))
        except ImmutableValueError as exc:
            raise _translate_error(exc) from exc
        applied = tuple(str(value) for value in self.applied_effects)
        if any(not value for value in applied) or len(applied) != len(
            set(applied)
        ):
            raise ReplacementEffectError(
                "A replacement effect cannot be journaled twice on one event"
            )
        object.__setattr__(self, "applied_effects", applied)
        children = tuple(self.children)
        if any(not isinstance(child, ReplaceableEvent) for child in children):
            raise ReplacementEffectError(
                "Nested replaceable events must be typed events"
            )
        object.__setattr__(self, "children", children)
        child_ids = [child.event_id for child in children]
        if len(child_ids) != len(set(child_ids)):
            raise ReplacementEffectError(
                "Nested replaceable event IDs must be unique"
            )

    @property
    def chooser(self) -> str:
        if self.affected_player is not None:
            return self.affected_player
        assert self.affected_object is not None
        return self.affected_object.chooser

    def with_payload(
        self,
        payload: Mapping[str, Any],
        *,
        applied_effect: str,
        children: Sequence["ReplaceableEvent"] | None = None,
        entry_scope: EntryReplacementScope | None = None,
    ) -> "ReplaceableEvent":
        return ReplaceableEvent(
            event_id=self.event_id,
            kind=self.kind,
            affected_player=self.affected_player,
            payload=payload,
            applied_effects=(*self.applied_effects, applied_effect),
            affected_object=self.affected_object,
            children=tuple(self.children if children is None else children),
            entry_scope=self.entry_scope if entry_scope is None else entry_scope,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "kind": self.kind,
            "affected_player": self.affected_player,
            "affected_object": (
                self.affected_object.to_dict()
                if self.affected_object is not None
                else None
            ),
            "payload": thaw_value(self.payload),
            "applied_effects": list(self.applied_effects),
            "children": [child.to_dict() for child in self.children],
            "entry_scope": (
                self.entry_scope.to_dict()
                if self.entry_scope is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReplaceableEvent":
        exact_fields(
            value,
            {
                "event_id",
                "kind",
                "affected_player",
                "affected_object",
                "payload",
                "applied_effects",
                "children",
                "entry_scope",
            },
            field_name="event",
        )
        affected_object = value["affected_object"]
        entry_scope = value["entry_scope"]
        if affected_object is not None and not isinstance(
            affected_object, Mapping
        ):
            raise ReplacementEffectError(
                "Replacement affected_object must be an object or null"
            )
        if entry_scope is not None and not isinstance(entry_scope, Mapping):
            raise ReplacementEffectError(
                "Replacement entry_scope must be an object or null"
            )
        payload = value["payload"]
        if not isinstance(payload, Mapping):
            raise ReplacementEffectError(
                "Replacement event payload must be an object"
            )
        return cls(
            event_id=str(value["event_id"] or ""),
            kind=str(value["kind"] or ""),
            affected_player=(
                str(value["affected_player"])
                if value["affected_player"] is not None
                else None
            ),
            affected_object=(
                AffectedObject.from_dict(affected_object)
                if isinstance(affected_object, Mapping)
                else None
            ),
            payload=payload,
            applied_effects=string_sequence(
                value["applied_effects"], field_name="applied_effects"
            ),
            children=tuple(
                cls.from_dict(child)
                for child in mapping_sequence(
                    value["children"], field_name="event children"
                )
            ),
            entry_scope=(
                EntryReplacementScope.from_dict(entry_scope)
                if isinstance(entry_scope, Mapping)
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class ReplacementEffect:
    effect_id: str
    source_id: str
    event_kind: str
    replacement_class: ReplacementClass
    conditions: Mapping[str, Any] = field(default_factory=dict)
    operations: tuple[ReplacementOperation | Mapping[str, Any], ...] = ()
    decline_operations: tuple[
        ReplacementOperation | Mapping[str, Any], ...
    ] = ()
    optional: bool = False
    chooser: str = "affected_player"
    label: str = ""
    application_group_id: str | None = None

    def __post_init__(self) -> None:
        if not self.effect_id or not self.source_id:
            raise ReplacementEffectError(
                "Replacement effects require stable IDs"
            )
        if self.effect_id.startswith("decline:"):
            raise ReplacementEffectError(
                "Replacement effect IDs cannot use the decline namespace"
            )
        if not self.event_kind:
            raise ReplacementEffectError(
                "Replacement effects require an event kind"
            )
        if self.chooser != "affected_player":
            raise ReplacementEffectError(
                "Only affected-player/object-controller choice is compiled"
            )
        if not isinstance(self.replacement_class, ReplacementClass):
            raise ReplacementEffectError(
                "Replacement effects require a typed replacement class"
            )
        if type(self.optional) is not bool:
            raise ReplacementEffectError(
                "Replacement effect optional must be a boolean"
            )
        if self.application_group_id is not None and (
            type(self.application_group_id) is not str
            or not self.application_group_id
        ):
            raise ReplacementEffectError(
                "Replacement application-group identity must be nonempty or null"
            )
        try:
            object.__setattr__(self, "conditions", FrozenMap(self.conditions))
            lowered = tuple(lower_operation(value) for value in self.operations)
            lowered_decline = tuple(
                lower_operation(value) for value in self.decline_operations
            )
        except (ImmutableValueError, ReplacementOperationError) as exc:
            raise _translate_error(exc) from exc
        if not lowered:
            raise ReplacementEffectError(
                "Replacement effects require operations"
            )
        if self.application_group_id is not None and (
            len(lowered) != 1
            or operation_to_dict(lowered[0]) != {"op": "prevent"}
            or lowered_decline
            or self.optional
        ):
            raise ReplacementEffectError(
                "Replacement application groups require mandatory prevent-all siblings"
            )
        object.__setattr__(self, "operations", lowered)
        if lowered_decline and not self.optional:
            raise ReplacementEffectError(
                "Only optional replacements may define decline operations"
            )
        object.__setattr__(self, "decline_operations", lowered_decline)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "effect_id": self.effect_id,
            "source_id": self.source_id,
            "event_kind": self.event_kind,
            "replacement_class": int(self.replacement_class),
            "conditions": thaw_value(self.conditions),
            "operations": [operation_to_dict(value) for value in self.operations],
            "optional": self.optional,
            "chooser": self.chooser,
            "label": self.label,
        }
        if self.decline_operations:
            result["decline_operations"] = [
                operation_to_dict(value)
                for value in self.decline_operations
            ]
        if self.application_group_id is not None:
            result["application_group_id"] = self.application_group_id
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReplacementEffect":
        required = {
                "effect_id",
                "source_id",
                "event_kind",
                "replacement_class",
                "conditions",
                "operations",
                "optional",
                "chooser",
                "label",
        }
        actual = set(value)
        missing = sorted(required - actual)
        unknown = sorted(
            actual
            - required
            - {"application_group_id", "decline_operations"}
        )
        if missing or unknown:
            details: list[str] = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if unknown:
                details.append("unknown " + ", ".join(unknown))
            raise ReplacementEffectError(
                f"Replacement effect fields: {'; '.join(details)}"
            )
        conditions = value["conditions"]
        if not isinstance(conditions, Mapping):
            raise ReplacementEffectError(
                "Replacement effect conditions must be an object"
            )
        if type(value["optional"]) is not bool:
            raise ReplacementEffectError(
                "Replacement effect optional must be a boolean"
            )
        try:
            replacement_class = ReplacementClass(
                int(value["replacement_class"])
            )
        except (TypeError, ValueError) as exc:
            raise ReplacementEffectError(
                "Replacement effect has an invalid replacement class"
            ) from exc
        return cls(
            effect_id=str(value["effect_id"] or ""),
            source_id=str(value["source_id"] or ""),
            event_kind=str(value["event_kind"] or ""),
            replacement_class=replacement_class,
            conditions=conditions,
            operations=tuple(
                mapping_sequence(
                    value["operations"], field_name="effect operations"
                )
            ),
            decline_operations=tuple(
                mapping_sequence(
                    value.get("decline_operations", ()),
                    field_name="effect decline operations",
                )
            ),
            optional=value["optional"],
            chooser=str(value["chooser"] or ""),
            label=str(value["label"] or ""),
            application_group_id=(
                str(value["application_group_id"])
                if value.get("application_group_id") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class ReplacementChoice:
    event: ReplaceableEvent
    chooser: str
    options: tuple[str, ...]
    optional_options: tuple[str, ...]
    replacement_class: ReplacementClass

    @property
    def legal_selections(self) -> tuple[str, ...]:
        result: list[str] = []
        for option in self.options:
            result.append(option)
            if option in self.optional_options:
                result.append(f"decline:{option}")
        return tuple(result)


@dataclass(frozen=True, slots=True)
class ReplacementTreeChoice:
    path: tuple[int, ...]
    choice: ReplacementChoice


@dataclass(frozen=True, slots=True)
class ReplacementSelection:
    event_id: str
    path: tuple[int, ...]
    chooser: str
    effect_id: str
    allocation: Mapping[str, int] | None = None

    def __post_init__(self) -> None:
        if not self.event_id or not self.chooser:
            raise ReplacementEffectError(
                "Replacement selections require event and chooser IDs"
            )
        path = tuple(self.path)
        if any(type(index) is not int or index < 0 for index in path):
            raise ReplacementEffectError(
                "Replacement selection paths require nonnegative integers"
            )
        object.__setattr__(self, "path", path)
        if not isinstance(self.effect_id, str) or not self.effect_id:
            raise ReplacementEffectError(
                "Replacement selection effect IDs must be canonical strings"
            )
        if self.allocation is not None:
            if not isinstance(self.allocation, Mapping):
                raise ReplacementEffectError(
                    "Replacement prevention allocation must be an object"
                )
            allocation: dict[str, int] = {}
            for event_id, amount in self.allocation.items():
                event_key = str(event_id or "")
                if (
                    not event_key
                    or type(amount) is not int
                    or amount < 0
                ):
                    raise ReplacementEffectError(
                        "Replacement prevention allocations require event IDs "
                        "and nonnegative integer amounts"
                    )
                allocation[event_key] = amount
            if not allocation or not any(allocation.values()):
                raise ReplacementEffectError(
                    "Replacement prevention allocation must prevent damage"
                )
            object.__setattr__(self, "allocation", FrozenMap(allocation))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "path": list(self.path),
            "chooser": self.chooser,
            "effect_id": self.effect_id,
            **(
                {"allocation": thaw_value(self.allocation)}
                if self.allocation is not None
                else {}
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReplacementSelection":
        expected = {"event_id", "path", "chooser", "effect_id"}
        if "allocation" in value:
            expected.add("allocation")
        exact_fields(value, expected, field_name="selection")
        if not isinstance(value["effect_id"], str):
            raise ReplacementEffectError(
                "Replacement selection effect IDs must be canonical strings"
            )
        path_values = sequence(value["path"], field_name="selection path")
        if any(type(item) is not int for item in path_values):
            raise ReplacementEffectError(
                "Replacement selection paths require integer indexes"
            )
        return cls(
            event_id=str(value["event_id"] or ""),
            path=tuple(path_values),
            chooser=str(value["chooser"] or ""),
            effect_id=value["effect_id"],
            allocation=(
                value["allocation"] if "allocation" in value else None
            ),
        )


def event_at_path(
    event: ReplaceableEvent, path: tuple[int, ...]
) -> ReplaceableEvent:
    current = event
    for index in path:
        if index < 0 or index >= len(current.children):
            raise ReplacementEffectError(
                "Replacement event path is no longer valid"
            )
        current = current.children[index]
    return current


def walk_events(event: ReplaceableEvent) -> Iterable[ReplaceableEvent]:
    yield event
    for child in event.children:
        yield from walk_events(child)


@dataclass(frozen=True, slots=True)
class ReplacementEventBatch:
    batch_id: str
    events: tuple[ReplaceableEvent, ...]
    apnap_order: tuple[str, ...]
    journal: tuple[ReplacementSelection, ...] = ()

    def __post_init__(self) -> None:
        events = tuple(self.events)
        order = tuple(str(value) for value in self.apnap_order)
        journal = tuple(self.journal)
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "apnap_order", order)
        object.__setattr__(self, "journal", journal)
        if not self.batch_id or not events:
            raise ReplacementEffectError(
                "A replacement batch requires an ID and at least one event"
            )
        if any(not isinstance(event, ReplaceableEvent) for event in events):
            raise ReplacementEffectError(
                "Replacement batches require typed events"
            )
        if not order or any(not value for value in order) or len(order) != len(
            set(order)
        ):
            raise ReplacementEffectError(
                "Replacement batch APNAP order must contain unique seats"
            )
        tree_events = [nested for event in events for nested in walk_events(event)]
        event_ids = [event.event_id for event in tree_events]
        if len(event_ids) != len(set(event_ids)):
            raise ReplacementEffectError(
                "Replacement event IDs must be unique across the batch tree"
            )
        unknown = sorted(
            {event.chooser for event in tree_events if event.chooser not in order}
        )
        if unknown:
            raise ReplacementEffectError(
                "Affected chooser(s) are missing from APNAP order: "
                + ", ".join(unknown)
            )
        roots = {event.event_id: event for event in events}
        for selection in journal:
            if not isinstance(selection, ReplacementSelection):
                raise ReplacementEffectError(
                    "Replacement journals require typed selections"
                )
            root = roots.get(selection.event_id)
            if root is None:
                raise ReplacementEffectError(
                    "Replacement journal event is absent from the batch"
                )
            selected_event = event_at_path(root, selection.path)
            applied_effect = selection.effect_id.removeprefix("decline:")
            chooser_at_application = dict(
                selected_event.payload.get("replacement_choosers") or {}
            ).get(applied_effect, selected_event.chooser)
            if chooser_at_application != selection.chooser:
                raise ReplacementEffectError(
                    "Replacement journal chooser does not match its event path"
                )
            if applied_effect not in selected_event.applied_effects:
                raise ReplacementEffectError(
                    "Replacement journal effect is not applied at its event path"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "events": [event.to_dict() for event in self.events],
            "apnap_order": list(self.apnap_order),
            "journal": [selection.to_dict() for selection in self.journal],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReplacementEventBatch":
        exact_fields(
            value,
            {"batch_id", "events", "apnap_order", "journal"},
            field_name="batch",
        )
        return cls(
            batch_id=str(value["batch_id"] or ""),
            events=tuple(
                ReplaceableEvent.from_dict(event)
                for event in mapping_sequence(
                    value["events"], field_name="batch events"
                )
            ),
            apnap_order=string_sequence(
                value["apnap_order"], field_name="APNAP order"
            ),
            journal=tuple(
                ReplacementSelection.from_dict(selection)
                for selection in mapping_sequence(
                    value["journal"], field_name="batch journal"
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ReplacementBatchChoice:
    batch_id: str
    event_index: int
    event_id: str
    tree_choice: ReplacementTreeChoice
    prior_public_choices: tuple[ReplacementSelection, ...]
    prevention_allocations: tuple["PreventionAllocationChoice", ...] = ()
    event_order_options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        options = tuple(self.event_order_options)
        if options and (
            len(options) < 2
            or len(options) != len(set(options))
            or self.event_id not in options
            or any(not value for value in options)
        ):
            raise ReplacementEffectError(
                "Replacement event-order options must be unique and include the current event"
            )
        object.__setattr__(self, "event_order_options", options)

    @property
    def choice(self) -> ReplacementChoice:
        return self.tree_choice.choice

    @property
    def path(self) -> tuple[int, ...]:
        return self.tree_choice.path


@dataclass(frozen=True, slots=True)
class PreventionAllocationChoice:
    effect_id: str
    shield_id: str
    available: int | None
    events: tuple[tuple[str, int, bool], ...]
    allocation_required: bool

    def __post_init__(self) -> None:
        if not self.effect_id or not self.shield_id or not self.events:
            raise ReplacementEffectError(
                "A prevention allocation choice requires stable IDs and events"
            )
        if self.available is not None and (
            type(self.available) is not int or self.available < 1
        ):
            raise ReplacementEffectError(
                "A prevention allocation requires a positive available amount"
            )
        event_ids = [event_id for event_id, _, _ in self.events]
        if len(event_ids) != len(set(event_ids)) or any(
            not event_id
            or type(amount) is not int
            or amount < 0
            or type(unpreventable) is not bool
            for event_id, amount, unpreventable in self.events
        ):
            raise ReplacementEffectError(
                "A prevention allocation requires unique valid damage events"
            )

    @property
    def automatic_allocation(self) -> dict[str, int] | None:
        preventable = [
            (event_id, amount)
            for event_id, amount, unpreventable in self.events
            if not unpreventable and amount > 0
        ]
        total = sum(amount for _, amount in preventable)
        if total == 0:
            return {}
        if self.available is None or total <= self.available:
            return dict(preventable)
        if len(preventable) == 1:
            event_id, amount = preventable[0]
            return {event_id: min(amount, self.available)}
        return None


@dataclass(frozen=True, slots=True)
class ReplacementBatchProgress:
    batch: ReplacementEventBatch
    pending: ReplacementBatchChoice | None
    consumed_selections: int = 0
