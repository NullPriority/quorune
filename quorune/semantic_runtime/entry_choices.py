from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement_effects import (
    CreateAffectedObjectCounter,
    GrantAffectedObjectKeyword,
    ReplacementClass,
    ReplacementEffect,
    SetField,
)
from ..read_ahead import READ_AHEAD_ENTRY_HANDLER_ID
from ..riot import RIOT_ENTRY_HANDLER_ID
from ..keyword_counters import (
    KeywordCounterError, FIXED_KEYWORD_COUNTER_CHOICE_CAPABILITY,
    FIXED_KEYWORD_COUNTER_CHOICE_HANDLER, KeywordCounterEntryChoiceSpec,
)
from .component_registry import exact_fields
from .context import SemanticNodeError
from .zone_replacement_model import (
    ZoneChangeReplacementContext,
    ZoneChangeSubjectSnapshot,
    ZoneDestinationIntent,
)


@dataclass(frozen=True, slots=True)
class RiotEntryChoiceNode:
    counter_name: str
    amount: int
    alternative_keyword: str
    rule_id: str

    def __post_init__(self) -> None:
        if type(self.counter_name) is not str:
            raise SemanticNodeError("Riot counter name must be a string")
        counter_name = " ".join(self.counter_name.casefold().split())
        if not counter_name:
            raise SemanticNodeError("Riot requires a counter name")
        if type(self.amount) is not int or self.amount < 1:
            raise SemanticNodeError(
                "Riot counter amount must be a positive integer"
            )
        if type(self.alternative_keyword) is not str:
            raise SemanticNodeError("Riot alternative keyword must be a string")
        alternative_keyword = " ".join(
            self.alternative_keyword.casefold().split()
        )
        if alternative_keyword != "haste":
            raise SemanticNodeError(
                "Riot alternative is outside the represented keyword vocabulary"
            )
        if type(self.rule_id) is not str or not self.rule_id.strip():
            raise SemanticNodeError("Riot requires rule identity")
        object.__setattr__(self, "counter_name", counter_name)
        object.__setattr__(self, "alternative_keyword", alternative_keyword)


@dataclass(frozen=True, slots=True)
class RiotEntryChoiceHandler:
    handler_id: str = RIOT_ENTRY_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.zone.riot-entry-choice"
    event: str = "zone.change"
    rule_references: tuple[str, ...] = (
        "122.6",
        "614.1c",
        "614.12",
        "614.16",
        "616.1",
        "702.10",
        "702.136a",
        "702.136b",
    )
    capability_dependencies: tuple[str, ...] = ("counter.producer.riot",)

    def validate(self, descriptor: Mapping[str, Any]) -> RiotEntryChoiceNode:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "counter_name",
                "amount",
                "alternative_keyword",
                "rule_id",
            },
            field="Riot entry choice",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Riot entry handler identity changed")
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError("Unsupported Riot entry schema version")
        if descriptor["event"] != self.event:
            raise SemanticNodeError("Riot must handle a zone change")
        return RiotEntryChoiceNode(
            counter_name=descriptor["counter_name"],
            amount=descriptor["amount"],
            alternative_keyword=descriptor["alternative_keyword"],
            rule_id=descriptor["rule_id"],
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ZoneChangeReplacementContext,
    ) -> tuple[ZoneDestinationIntent, ...]:
        self.validate(descriptor)
        return ()

    def subject_replacement_effect(
        self,
        descriptor: Mapping[str, Any],
        *,
        subject: ZoneChangeSubjectSnapshot,
        component_id: str,
    ) -> ReplacementEffect:
        node = self.validate(descriptor)
        if subject.destination_controller is None:
            raise SemanticNodeError(
                "Riot battlefield entry requires a destination controller"
            )
        if not component_id:
            raise SemanticNodeError("Riot requires stable component identity")
        return ReplacementEffect(
            effect_id=f"{self.handler_id}:{subject.object_ref}:{component_id}",
            source_id=subject.object_ref,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "destination": {"eq": "battlefield"},
                "object_ref": {"eq": subject.object_ref},
            },
            operations=(
                CreateAffectedObjectCounter(
                    counter_name=node.counter_name,
                    amount=node.amount,
                    placing_player=subject.destination_controller,
                    source_ref=subject.object_ref,
                    sequence=0,
                ),
            ),
            decline_operations=(
                GrantAffectedObjectKeyword(
                    keyword=node.alternative_keyword,
                    sequence=0,
                ),
            ),
            optional=True,
            label=(
                f"{subject.object_ref}: enter with {node.amount} "
                f"{node.counter_name} counter(s); otherwise gain haste"
            ),
        )


@dataclass(frozen=True, slots=True)
class KeywordCounterEntryChoiceHandler:
    handler_id: str = FIXED_KEYWORD_COUNTER_CHOICE_HANDLER
    schema_version: int = 1
    family: str = "replacement.zone.keyword-counter-entry-choice"
    event: str = "zone.change"
    rule_references: tuple[str, ...] = (
        "122.1b", "122.6", "400.7", "614.1c", "614.12", "614.16", "616.1",
    )
    capability_dependencies: tuple[str, ...] = (
        FIXED_KEYWORD_COUNTER_CHOICE_CAPABILITY, "counter.placement.quantity_replacement",
        "counter.characteristic.keyword",
    )

    def validate(self, descriptor: Mapping[str, Any]) -> KeywordCounterEntryChoiceSpec:
        try:
            return KeywordCounterEntryChoiceSpec.from_descriptor(descriptor)
        except KeywordCounterError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor: Mapping[str, Any], context: ZoneChangeReplacementContext
              ) -> tuple[ZoneDestinationIntent, ...]:
        self.validate(descriptor)
        return ()

    def subject_replacement_effect(self, descriptor: Mapping[str, Any], *,
                                  subject: ZoneChangeSubjectSnapshot,
                                  component_id: str) -> ReplacementEffect:
        spec = self.validate(descriptor)
        if subject.destination_controller is None or not component_id:
            raise SemanticNodeError("Entry counter choice requires a controller and component identity")
        first, second = spec.counter_names

        def counter(name: str) -> CreateAffectedObjectCounter:
            return CreateAffectedObjectCounter(counter_name=name, amount=1,
                placing_player=subject.destination_controller, source_ref=subject.object_ref, sequence=0)

        return ReplacementEffect(
            effect_id=f"{self.handler_id}:{subject.object_ref}:{component_id}",
            source_id=subject.object_ref, event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions={"destination": {"eq": "battlefield"}, "object_ref": {"eq": subject.object_ref}},
            operations=(counter(first),), decline_operations=(counter(second),), optional=True,
            label=f"Enter with a {first} counter", decline_label=f"Enter with a {second} counter",
        )


@dataclass(frozen=True, slots=True)
class ReadAheadEntryChoiceNode:
    chapter_numbers: tuple[int, ...]
    counter_name: str
    rule_id: str

    def __post_init__(self) -> None:
        chapters = tuple(self.chapter_numbers)
        if (
            not chapters
            or any(type(value) is not int or value < 1 for value in chapters)
            or chapters != tuple(range(1, chapters[-1] + 1))
        ):
            raise SemanticNodeError(
                "Read Ahead requires contiguous positive chapter numbers"
            )
        if self.counter_name != "lore":
            raise SemanticNodeError("Read Ahead requires lore counters")
        if self.rule_id != "714.3b":
            raise SemanticNodeError("Read Ahead rule identity changed")
        object.__setattr__(self, "chapter_numbers", chapters)


@dataclass(frozen=True, slots=True)
class ReadAheadEntryChoiceHandler:
    handler_id: str = READ_AHEAD_ENTRY_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.zone.read-ahead-entry-choice"
    event: str = "zone.change"
    rule_references: tuple[str, ...] = (
        "122.6",
        "614.1c",
        "614.12",
        "614.16",
        "616.1",
        "702.155",
        "702.155a",
        "702.155b",
        "702.155c",
        "714.2b",
        "714.2d",
        "714.3b",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.producer.saga_lore",
        "state_based.saga_final_chapter",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> ReadAheadEntryChoiceNode:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "chapter_numbers",
                "counter_name",
                "rule_id",
            },
            field="Read Ahead entry choice",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Read Ahead entry handler identity changed")
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError(
                "Unsupported Read Ahead entry schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError("Read Ahead must handle a zone change")
        raw_chapters = descriptor["chapter_numbers"]
        if not isinstance(raw_chapters, (list, tuple)):
            raise SemanticNodeError(
                "Read Ahead chapter numbers must be an array"
            )
        return ReadAheadEntryChoiceNode(
            chapter_numbers=tuple(raw_chapters),
            counter_name=descriptor["counter_name"],
            rule_id=descriptor["rule_id"],
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ZoneChangeReplacementContext,
    ) -> tuple[ZoneDestinationIntent, ...]:
        self.validate(descriptor)
        return ()

    def subject_replacement_effects(
        self,
        descriptor: Mapping[str, Any],
        *,
        subject: ZoneChangeSubjectSnapshot,
        component_id: str,
    ) -> tuple[ReplacementEffect, ...]:
        node = self.validate(descriptor)
        if subject.destination_controller is None:
            raise SemanticNodeError(
                "Read Ahead battlefield entry requires a controller"
            )
        if not subject.is_card_object:
            raise SemanticNodeError(
                "Read Ahead copied tokens are outside the represented boundary"
            )
        if "saga" not in subject.object_types:
            raise SemanticNodeError("Read Ahead requires a Saga object")
        if not component_id:
            raise SemanticNodeError(
                "Read Ahead requires stable component identity"
            )
        source_ref = f"rule:{node.rule_id}:{subject.object_ref}"
        return tuple(
            ReplacementEffect(
                effect_id=(
                    f"{self.handler_id}:{subject.object_ref}:"
                    f"{component_id}:chapter:{chapter}"
                ),
                source_id=source_ref,
                event_kind=self.event,
                replacement_class=ReplacementClass.SELF_REPLACEMENT,
                conditions={
                    "destination": {"eq": "battlefield"},
                    "object_ref": {"eq": subject.object_ref},
                    "object_types": {"contains": "saga"},
                    "read_ahead_chapter": {"eq": None},
                },
                operations=(
                    SetField("read_ahead_chapter", chapter),
                    CreateAffectedObjectCounter(
                        counter_name=node.counter_name,
                        amount=chapter,
                        placing_player=subject.destination_controller,
                        source_ref=source_ref,
                        sequence=0,
                    ),
                ),
                label=(
                    f"{subject.object_ref}: read ahead to chapter {chapter}"
                ),
            )
            for chapter in node.chapter_numbers
        )


__all__ = [
    "KeywordCounterEntryChoiceHandler",
    "ReadAheadEntryChoiceHandler",
    "ReadAheadEntryChoiceNode",
    "RiotEntryChoiceHandler",
    "RiotEntryChoiceNode",
]
