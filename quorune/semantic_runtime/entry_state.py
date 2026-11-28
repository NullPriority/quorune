from __future__ import annotations

"""Typed CR 614/616 battlefield-entry tap-state replacements."""

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..entry_state_conditions import (
    FIXED_ENTRY_CONDITION_HANDLER_ID,
    FixedEntryCondition,
    FixedEntryConditionError,
)
from ..landwalk import BASIC_LAND_TYPES
from ..rad_counter_model import (
    ENTRY_RAD_CHOICE_DEPENDENCIES, ENTRY_RAD_CHOICE_FIELD, ENTRY_RAD_CHOICE_HANDLER,
    ENTRY_RAD_COUNTER_CHILD, RAD_COUNTER, entry_rad_choice_descriptor,
)
from ..replacement import AddAmount, CreateNestedEvent, ReplacementClass, ReplacementEffect, SetField
from ..replacement.entry_counter_types import BATTLEFIELD_ENTRY_COUNTER_CHILD
from ..replacement.immutable import FrozenMap
from .component_registry import exact_fields
from .context import SemanticNodeError


ENTRY_STATE_HANDLER_ID = "replacement.zone.entry-state.v1"
_SOURCE_RELATIONS = frozenset({"affected_object", "controller"})


@dataclass(frozen=True, slots=True)
class EntryRadChoiceHandler:
    handler_id: str = ENTRY_RAD_CHOICE_HANDLER
    schema_version: int = 1
    family: str = "replacement.zone.entry_tapped_rad_choice"
    event: str = "zone.change"
    rule_references: tuple[str, ...] = ("614.1c", "614.12", "616.1", "728.1")
    capability_dependencies: tuple[str, ...] = ENTRY_RAD_CHOICE_DEPENDENCIES

    def validate(self, descriptor):
        if (FrozenMap(descriptor) != FrozenMap(entry_rad_choice_descriptor())
                or type(descriptor.get("schema_version")) is not int
                or type(descriptor.get("amount")) is not int):
            raise SemanticNodeError("An optional tapped entry requires its exact land and rad counters")

    def lower(self, descriptor, context):
        self.validate(descriptor)
        return ()

    def subject_replacement_effects(self, descriptor, *, subject, component_id, host, simultaneous_departures=()):
        self.validate(descriptor)
        if subject.destination != "battlefield" or "land" not in subject.object_types:
            return ()
        return tuple(
            _entry_rad_alternative(subject, component_id, seat, accept=accept)
            for seat in host.active_seats
            for accept in (True, False)
        )


def _entry_rad_alternative(subject, component_id, seat, *, accept):
    operations = (SetField(ENTRY_RAD_CHOICE_FIELD, accept),)
    if accept:
        from .counter_replacements import CounterPlacementEventSpec

        counters = CounterPlacementEventSpec(
            event_id=f"entry-rad:{subject.logical_object_id}:{component_id}:{seat}",
            subject_kind="player", subject_id=seat, placing_player=seat,
            counter_name=RAD_COUNTER, amount=2, source_ref=subject.object_ref, effect_generated=True,
        ).event()
        operations += (
            SetField("tapped", True),
            CreateNestedEvent({
                "kind": counters.kind, "affected_player": seat,
                "payload": {**counters.payload, ENTRY_RAD_COUNTER_CHILD: True, BATTLEFIELD_ENTRY_COUNTER_CHILD: True},
            }),
        )
    return ReplacementEffect(
        effect_id=f"{ENTRY_RAD_CHOICE_HANDLER}:{component_id}:{subject.object_ref}:{seat}:{int(accept)}",
        source_id=subject.object_ref, event_kind="zone.change", replacement_class=ReplacementClass.OTHER,
        conditions={
            "destination": {"eq": "battlefield"}, "object_ref": {"eq": subject.object_ref},
            "destination_controller": {"eq": seat}, ENTRY_RAD_CHOICE_FIELD: {"eq": None},
        },
        operations=operations,
        label="Enter tapped and get two rad counters" if accept else "Enter without getting rad counters",
    )


@dataclass(frozen=True, slots=True)
class EntryStateNode:
    source_relation: str
    subject_types: tuple[str, ...]
    minimum_opponents: int | None
    controlled_basic_types_any: tuple[str, ...]
    tapped: bool
    optional_life: int


@dataclass(frozen=True, slots=True)
class EntryStateSourceContext:
    source_ref: str
    source_controller: str
    component_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "source_ref",
            "source_controller",
            "component_id",
        ):
            value = getattr(self, field_name)
            if type(value) is not str or not value:
                raise SemanticNodeError(
                    f"Entry-state {field_name} must be a nonempty string"
                )


@dataclass(frozen=True, slots=True)
class EntryStateSubjectContext:
    object_ref: str
    destination_controller: str
    object_types: tuple[str, ...]
    opponent_count: int
    controlled_basic_land_types: tuple[str, ...]
    pay_life: bool | None
    component_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "object_ref",
            "destination_controller",
            "component_id",
        ):
            value = getattr(self, field_name)
            if type(value) is not str or not value:
                raise SemanticNodeError(
                    f"Entry-state {field_name} must be a nonempty string"
                )
        if type(self.opponent_count) is not int or self.opponent_count < 0:
            raise SemanticNodeError(
                "Entry-state opponent count must be a nonnegative integer"
            )
        for field_name in (
            "object_types",
            "controlled_basic_land_types",
        ):
            values = getattr(self, field_name)
            if (
                not isinstance(values, tuple)
                or any(type(value) is not str or not value for value in values)
                or len(values) != len(set(values))
            ):
                raise SemanticNodeError(
                    f"Entry-state {field_name} must contain unique strings"
                )
        if self.pay_life is not None and type(self.pay_life) is not bool:
            raise SemanticNodeError(
                "Entry-state life choice must be a boolean or null"
            )


@dataclass(frozen=True, slots=True)
class EntryStateReplacementHandler:
    handler_id: str = ENTRY_STATE_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.zone.entry_state"
    event: str = "zone.change"
    rule_references: tuple[str, ...] = (
        "614.1c",
        "614.1d",
        "614.12",
        "616.1",
    )
    capability_dependencies: tuple[str, ...] = (
        "zone.entry.tapped_state",
    )

    def validate(self, descriptor: Mapping[str, Any]) -> EntryStateNode:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "source_relation",
                "subject",
                "condition",
                "instruction",
            },
            field="entry-state runtime handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError(
                "Runtime handler ID does not match the entry-state registry"
            )
        if (
            type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
        ):
            raise SemanticNodeError(
                f"Unsupported {self.handler_id} schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                f"{self.handler_id} must handle {self.event}"
            )
        relation = descriptor["source_relation"]
        if type(relation) is not str or relation not in _SOURCE_RELATIONS:
            raise SemanticNodeError(
                "Entry-state source relation is unsupported"
            )

        subject = descriptor["subject"]
        if not isinstance(subject, Mapping):
            raise SemanticNodeError("Entry-state subject must be an object")
        exact_fields(subject, {"types_all"}, field="entry-state subject")
        raw_types = subject["types_all"]
        if not isinstance(raw_types, list) or any(
            type(value) is not str or not value for value in raw_types
        ):
            raise SemanticNodeError(
                "Entry-state subject types must be an array of strings"
            )
        subject_types = tuple(sorted(set(raw_types)))
        if len(subject_types) != len(raw_types) or any(
            value not in {"land"} for value in subject_types
        ):
            raise SemanticNodeError(
                "Entry-state subject types are outside the represented vocabulary"
            )

        condition = descriptor["condition"]
        if not isinstance(condition, Mapping):
            raise SemanticNodeError("Entry-state condition must be an object")
        exact_fields(
            condition,
            {"minimum_opponents", "controlled_basic_types_any"},
            field="entry-state condition",
        )
        minimum_opponents = condition["minimum_opponents"]
        if minimum_opponents is not None and (
            type(minimum_opponents) is not int or minimum_opponents < 1
        ):
            raise SemanticNodeError(
                "Entry-state opponent thresholds must be positive integers"
            )
        raw_basic_types = condition["controlled_basic_types_any"]
        if not isinstance(raw_basic_types, list) or any(
            type(value) is not str or value not in BASIC_LAND_TYPES
            for value in raw_basic_types
        ):
            raise SemanticNodeError(
                "Entry-state controlled types must be basic land types"
            )
        controlled_basic_types = tuple(sorted(set(raw_basic_types)))
        if len(controlled_basic_types) != len(raw_basic_types):
            raise SemanticNodeError(
                "Entry-state controlled types must be unique"
            )

        instruction = descriptor["instruction"]
        if not isinstance(instruction, Mapping):
            raise SemanticNodeError(
                "Entry-state instruction must be an object"
            )
        exact_fields(
            instruction,
            {"tapped", "optional_life"},
            field="entry-state instruction",
        )
        tapped = instruction["tapped"]
        optional_life = instruction["optional_life"]
        if type(tapped) is not bool:
            raise SemanticNodeError(
                "Entry-state tapped instruction must be boolean"
            )
        if type(optional_life) is not int or optional_life < 0:
            raise SemanticNodeError(
                "Entry-state optional life must be a nonnegative integer"
            )

        conditional_count = sum(
            (
                minimum_opponents is not None,
                bool(controlled_basic_types),
                optional_life > 0,
            )
        )
        if conditional_count > 1:
            raise SemanticNodeError(
                "Entry-state descriptors support one closed condition"
            )
        if relation == "controller":
            if (
                subject_types != ("land",)
                or tapped
                or conditional_count
            ):
                raise SemanticNodeError(
                    "Controller entry-state effects are limited to lands entering untapped"
                )
        elif not tapped:
            raise SemanticNodeError(
                "Affected-object entry-state effects must set tapped"
            )
        if conditional_count and subject_types != ("land",):
            raise SemanticNodeError(
                "Conditional entry-state effects require a land subject"
            )
        return EntryStateNode(
            source_relation=relation,
            subject_types=subject_types,
            minimum_opponents=minimum_opponents,
            controlled_basic_types_any=controlled_basic_types,
            tapped=tapped,
            optional_life=optional_life,
        )

    def source_replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        *,
        source_ref: str,
        source_controller: str,
        component_id: str,
    ) -> ReplacementEffect:
        node = self.validate(descriptor)
        if node.source_relation != "controller":
            raise SemanticNodeError(
                "Affected-object entry state cannot lower as an ambient source"
            )
        context = EntryStateSourceContext(
            source_ref=source_ref,
            source_controller=source_controller,
            component_id=component_id,
        )
        return ReplacementEffect(
            effect_id=(
                f"{self.handler_id}:{context.source_ref}:"
                f"{context.component_id}"
            ),
            source_id=context.source_ref,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "destination": {"eq": "battlefield"},
                "destination_controller": {
                    "eq": context.source_controller
                },
                "object_types": {
                    "contains_all": list(node.subject_types)
                },
            },
            operations=(SetField("tapped", node.tapped),),
            label=f"{context.source_ref}: enter untapped",
        )

    def subject_replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        *,
        subject: Any,
        component_id: str,
    ) -> ReplacementEffect | None:
        node = self.validate(descriptor)
        if node.source_relation != "affected_object":
            raise SemanticNodeError(
                "Controller entry state cannot lower as an affected-object source"
            )
        if subject.destination_controller is None:
            return None
        context = EntryStateSubjectContext(
            object_ref=subject.object_ref,
            destination_controller=subject.destination_controller,
            object_types=tuple(subject.object_types),
            opponent_count=subject.opponent_count,
            controlled_basic_land_types=tuple(
                subject.controller_basic_land_types
            ),
            pay_life=subject.entry_pay_life,
            component_id=component_id,
        )
        if not set(node.subject_types).issubset(context.object_types):
            return None
        if (
            node.minimum_opponents is not None
            and context.opponent_count >= node.minimum_opponents
        ):
            return None
        if set(node.controlled_basic_types_any).intersection(
            context.controlled_basic_land_types
        ):
            return None
        if node.optional_life and context.pay_life is None:
            raise SemanticNodeError(
                "Optional entry life payment requires an explicit player choice"
            )
        operations: Sequence[Any]
        label: str
        if node.optional_life and context.pay_life:
            operations = (
                AddAmount("entry_life_payment", node.optional_life),
            )
            label = (
                f"{context.object_ref}: pay {node.optional_life} life as it enters"
            )
        else:
            operations = (SetField("tapped", node.tapped),)
            label = f"{context.object_ref}: enter tapped"
        return ReplacementEffect(
            effect_id=(
                f"{self.handler_id}:{context.object_ref}:"
                f"{context.component_id}"
            ),
            source_id=context.object_ref,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "destination": {"eq": "battlefield"},
                "object_ref": {"eq": context.object_ref},
                "object_types": {
                    "contains_all": list(node.subject_types)
                },
            },
            operations=tuple(operations),
            label=label,
        )

    def optional_life_amount(
        self,
        descriptor: Mapping[str, Any],
    ) -> int:
        node = self.validate(descriptor)
        return node.optional_life


@dataclass(frozen=True, slots=True)
class FixedEntryConditionReplacementHandler:
    handler_id: str = FIXED_ENTRY_CONDITION_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.zone.entry_state"
    event: str = "zone.change"
    rule_references: tuple[str, ...] = (
        "614.1c",
        "614.1d",
        "614.12",
        "616.1",
    )
    capability_dependencies: tuple[str, ...] = (
        "zone.entry.tapped_state.fixed_condition",
    )

    def validate(
        self,
        descriptor: Mapping[str, Any],
    ) -> FixedEntryCondition:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "subject",
                "condition",
            },
            field="fixed entry-condition runtime handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError(
                "Runtime handler ID does not match fixed entry conditions"
            )
        if (
            type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
        ):
            raise SemanticNodeError(
                "Unsupported fixed entry-condition schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                "Fixed entry conditions must handle zone.change"
            )
        subject = descriptor["subject"]
        if not isinstance(subject, Mapping):
            raise SemanticNodeError(
                "Fixed entry-condition subject must be an object"
            )
        exact_fields(
            subject,
            {"types_all"},
            field="fixed entry-condition subject",
        )
        if subject["types_all"] != ["land"]:
            raise SemanticNodeError(
                "Fixed entry conditions require a land subject"
            )
        try:
            return FixedEntryCondition.from_dict(descriptor["condition"])
        except FixedEntryConditionError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def source_replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        **kwargs: Any,
    ) -> ReplacementEffect:
        del kwargs
        self.validate(descriptor)
        raise SemanticNodeError(
            "Fixed entry conditions belong only to the affected object"
        )

    def subject_replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        *,
        subject: Any,
        component_id: str,
    ) -> ReplacementEffect | None:
        condition = self.validate(descriptor)
        if (
            subject.destination != "battlefield"
            or subject.destination_controller is None
            or "land" not in subject.object_types
        ):
            return None
        try:
            condition_met = condition.is_met(subject.entry_condition_metrics)
        except FixedEntryConditionError as exc:
            raise SemanticNodeError(str(exc)) from exc
        if condition_met is not condition.tapped_when_met:
            return None
        return ReplacementEffect(
            effect_id=(
                f"{self.handler_id}:{subject.object_ref}:{component_id}"
            ),
            source_id=subject.object_ref,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "destination": {"eq": "battlefield"},
                "object_ref": {"eq": subject.object_ref},
                "object_types": {"contains_all": ["land"]},
            },
            operations=(SetField("tapped", True),),
            label=f"{subject.object_ref}: enter tapped",
        )


__all__ = [
    "ENTRY_STATE_HANDLER_ID",
    "FIXED_ENTRY_CONDITION_HANDLER_ID",
    "EntryStateNode",
    "EntryStateReplacementHandler",
    "EntryStateSourceContext",
    "EntryStateSubjectContext",
    "FixedEntryConditionReplacementHandler",
]
