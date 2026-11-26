from __future__ import annotations

"""Intrinsic entry choices using the canonical replacement journal."""

from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Any, Mapping

from ..entry_designations import (
    ENTRY_CARD_NAME_FIELD, ENTRY_CARD_NAME_HANDLER_ID, EntryCardNameChoice,
    COUNTED_CREATURE_TYPE_ENTRY_CAPABILITY,
    COUNTED_CREATURE_TYPE_ENTRY_HANDLER_ID,
    COUNTED_ENTRY_OPPONENT_DEPENDENCIES,
    COUNTED_ENTRY_OPPONENT_HANDLER_ID,
    ENTRY_DESIGNATION_CAPABILITY,
    ENTRY_DESIGNATION_HANDLER_ID,
    ENTRY_OPPONENT_FIELD,
    ENTRY_OPPONENT_HANDLER_ID,
    ENTRY_PLAYER_HANDLER_ID,
    EntryOpponentChoice,
    entry_opponent_effect_id,
    LIMITED_CREATURE_TYPE_ENTRY_CAPABILITY,
    LIMITED_CREATURE_TYPE_ENTRY_HANDLER_ID,
    SELF_CHOSEN_SUBTYPE_CAPABILITY, SELF_CHOSEN_SUBTYPE_ENTRY_HANDLER_ID,
    EntryDesignationKind,
    validate_counted_entry_counter_name,
    validate_designation,
)
from ..keyword_counters import keyword_counter_mechanic
from ..linked_card_type import (
    CAPABILITY as LINKED_CARD_TYPE_CAPABILITY, ENTRY_FIELD as LINKED_CARD_TYPE_ENTRY_FIELD,
    ENTRY_HANDLER as LINKED_CARD_TYPE_ENTRY_HANDLER, OPTIONS as LINKED_CARD_TYPE_OPTIONS,
    LinkedCardTypeChoice,
)
from ..replacement.counted_entry_counters import CreateCountedEntryCounters
from ..replacement_effects import ReplacementClass, ReplacementEffect, SetField
from ..replacement.operations import UnionValues
from ..shared_creature_type_model import canonical_creature_types
from .component_registry import exact_fields
from .context import SemanticNodeError
from .zone_replacement_model import (
    ZoneChangeReplacementContext,
    ZoneChangeSubjectSnapshot,
    ZoneDestinationIntent,
)


@dataclass(frozen=True, slots=True)
class EntryDesignationHandler:
    handler_id: str = ENTRY_DESIGNATION_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.zone.entry_designation"
    event: str = "zone.change"
    rule_references: tuple[str, ...] = (
        "105.1", "205.3m", "400.7", "614.1c", "614.12", "614.12a", "616.1", "707.2",
    )
    capability_dependencies: tuple[str, ...] = (ENTRY_DESIGNATION_CAPABILITY,)

    def validate(self, descriptor: Mapping[str, Any]) -> EntryDesignationKind:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "designation"},
            field="entry designation",
        )
        if (
            descriptor["handler_id"] != self.handler_id
            or type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
            or descriptor["event"] != self.event
        ):
            raise SemanticNodeError("Entry designation handler identity changed")
        try:
            return EntryDesignationKind(descriptor["designation"])
        except (ValueError, TypeError) as exc:
            raise SemanticNodeError("Entry designation vocabulary is unsupported") from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ZoneChangeReplacementContext,
    ) -> tuple[ZoneDestinationIntent, ...]:
        self.validate(descriptor)
        return ()

    def _designation_values(self, descriptor, kind):
        return kind.values

    def subject_replacement_effects(
        self,
        descriptor: Mapping[str, Any],
        *,
        subject: ZoneChangeSubjectSnapshot,
        component_id: str,
    ) -> tuple[ReplacementEffect, ...]:
        kind = self.validate(descriptor)
        if subject.destination_controller is None or not component_id:
            raise SemanticNodeError(
                "Intrinsic entry choice requires a destination controller and component"
            )
        return tuple(
            self._designation_effect(subject, component_id, kind, value)
            for value in self._designation_values(descriptor, kind)
        )

    def _designation_effect(self, subject, component_id, kind, value, *, followups=()):
        return ReplacementEffect(
            effect_id=f"{self.handler_id}:{subject.object_ref}:{component_id}:{value}",
            source_id=subject.object_ref,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "destination": {"eq": "battlefield"},
                "object_ref": {"eq": subject.object_ref},
                kind.event_field: {"eq": None},
            },
            operations=(SetField(kind.event_field, value), *followups),
            label=f"{subject.object_ref}: choose {kind.value.replace('_', ' ')} {value}",
        )


@dataclass(frozen=True, slots=True)
class LinkedCardTypeEntryHandler(EntryDesignationHandler):
    handler_id: str = LINKED_CARD_TYPE_ENTRY_HANDLER
    rule_references: tuple[str, ...] = ("205.2a", "400.7", "607.2d", "614.12", "614.12a", "616.1", "707.6")
    capability_dependencies: tuple[str, ...] = (ENTRY_DESIGNATION_CAPABILITY, LINKED_CARD_TYPE_CAPABILITY)

    def validate(self, descriptor):
        exact_fields(descriptor, {"handler_id", "schema_version", "event"}, field="linked card-type entry")
        if (descriptor["handler_id"], type(descriptor["schema_version"]),
                descriptor["schema_version"], descriptor["event"]) != (self.handler_id, int, 1, self.event):
            raise SemanticNodeError("Linked card-type entry requires its exact replacement descriptor")

    def subject_replacement_effects(self, descriptor, *, subject, component_id):
        self.validate(descriptor)
        if subject.destination_controller is None:
            raise SemanticNodeError("Linked card-type entry requires a destination controller")
        choices = (LinkedCardTypeChoice(card_type, component_id, subject.logical_object_id)
                   for card_type in LINKED_CARD_TYPE_OPTIONS)
        return tuple(ReplacementEffect(
            effect_id=choice.effect_id(subject.object_ref),
            source_id=subject.object_ref,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "destination": {"eq": "battlefield"},
                "object_ref": {"eq": subject.object_ref},
                LINKED_CARD_TYPE_ENTRY_FIELD: {"eq": None},
            },
            operations=(SetField(LINKED_CARD_TYPE_ENTRY_FIELD, choice.to_dict()),),
            label=f"{subject.object_ref}: choose card type {choice.card_type}",
        ) for choice in choices)


@dataclass(frozen=True, slots=True)
class EntryCardNameHandler:
    handler_id: str = ENTRY_CARD_NAME_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.zone.entry_card_name"
    event: str = "zone.change"
    rule_references: tuple[str, ...] = ("201.4", "201.4a", "400.7", "614.12", "614.12a", "616.1")
    capability_dependencies: tuple[str, ...] = (ENTRY_DESIGNATION_CAPABILITY,)

    def validate(self, descriptor: Mapping[str, Any]) -> EntryCardNameChoice:
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "nonland"}, field="entry card name")
        if (descriptor["handler_id"] != self.handler_id or descriptor["event"] != self.event
                or type(descriptor["schema_version"]) is not int or descriptor["schema_version"] != 1):
            raise SemanticNodeError("Entry card name handler identity changed")
        return EntryCardNameChoice(descriptor["nonland"])

    def lower(self, descriptor, context) -> tuple[ZoneDestinationIntent, ...]:
        self.validate(descriptor)
        return ()

    def subject_replacement_effects(self, descriptor, *, subject, component_id):
        operation = self.validate(descriptor)
        if subject.destination != "battlefield" or subject.origin == "battlefield":
            return ()
        if subject.destination_controller is None or not component_id:
            raise SemanticNodeError("Entry name requires the destination controller and original component")
        return (ReplacementEffect(
            effect_id=f"{self.handler_id}:{subject.object_ref}:{component_id}",
            source_id=subject.object_ref, event_kind=self.event, replacement_class=ReplacementClass.OTHER,
            conditions={"destination": {"eq": "battlefield"}, "object_ref": {"eq": subject.object_ref},
                        ENTRY_CARD_NAME_FIELD: {"eq": None}},
            operations=(operation,),
            label="Choose a nonland card name" if operation.nonland else "Choose a card name",
        ),)


@dataclass(frozen=True, slots=True)
class EntryOpponentDesignationHandler:
    handler_id: str = ENTRY_OPPONENT_HANDLER_ID
    schema_version: int = 1
    family: str = "replacement.zone.linked_entry_opponent"
    event: str = "zone.change"
    rule_references: tuple[str, ...] = ("400.7", "607.2d", "614.12", "614.12a", "616.1")
    capability_dependencies: tuple[str, ...] = (ENTRY_DESIGNATION_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {"handler_id", "schema_version", "event"}, field="linked entry opponent")
        if (descriptor["handler_id"] != self.handler_id or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version or descriptor["event"] != self.event):
            raise SemanticNodeError("Linked entry opponent handler identity changed")
        return self

    def lower(self, descriptor, context):
        self.validate(descriptor)
        return ()

    def subject_replacement_effects(self, descriptor, *, subject, component_id, host, simultaneous_departures=()):
        self.validate(descriptor)
        if subject.destination != "battlefield" or subject.origin == "battlefield":
            return ()
        if host is None or subject.destination_controller not in host.active_seats:
            raise SemanticNodeError("An entry opponent choice requires its live destination controller")
        return tuple(
            self._choice_effect(subject, EntryOpponentChoice(seat, component_id, schema_version=self.schema_version))
            for seat in host.active_seats if self.schema_version == 2 or seat != subject.destination_controller
        )

    def _choice_effect(self, subject, choice):
        relation = "player" if choice.any_player else "opponent"
        return ReplacementEffect(
            effect_id=entry_opponent_effect_id(subject.object_ref, choice),
            source_id=subject.object_ref,
            event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "destination": {"eq": "battlefield"},
                "object_ref": {"eq": subject.object_ref},
                ENTRY_OPPONENT_FIELD: {"eq": None},
            },
            operations=(SetField(ENTRY_OPPONENT_FIELD, choice.to_dict()),),
            label=f"{subject.object_ref}: choose {relation} {choice.seat}",
        )


@dataclass(frozen=True, slots=True)
class EntryPlayerDesignationHandler(EntryOpponentDesignationHandler):
    handler_id: str = ENTRY_PLAYER_HANDLER_ID
    schema_version: int = 2
    family: str = "replacement.zone.linked_entry_player"


def _controlled_entry_creatures(host, controller):
    for object_id in host.state.players[controller].zones["battlefield"]:
        card = host.state.cards[object_id]
        if card.zone != "battlefield" or card.controller != controller or card.phased_out:
            continue
        data = host._effective_card_data(card)
        types, subtypes, _ = host._type_parts(str(data.get("type_line") or ""))
        if "creature" in types:
            yield object_id, subtypes


@dataclass(frozen=True, slots=True)
class CountedEntryOpponentHandler(EntryOpponentDesignationHandler):
    handler_id: str = COUNTED_ENTRY_OPPONENT_HANDLER_ID
    family: str = "replacement.zone.counted_entry_opponent"
    rule_references: tuple[str, ...] = ("109.5", "122.6", "400.7", "607.2d", "614.12", "614.12a", "616.1")
    capability_dependencies: tuple[str, ...] = COUNTED_ENTRY_OPPONENT_DEPENDENCIES

    def subject_replacement_effects(self, descriptor, *, subject, component_id, host, simultaneous_departures=()):
        choices = super(CountedEntryOpponentHandler, self).subject_replacement_effects(
            descriptor, subject=subject, component_id=component_id, host=host,
            simultaneous_departures=simultaneous_departures,
        )
        concurrent = set(simultaneous_departures)
        prepared = []
        for effect in choices:
            choice = EntryOpponentChoice.from_dict(effect.operations[0].value)
            matching = tuple(sorted(object_id for object_id, _ in _controlled_entry_creatures(host, choice.seat)))
            if matching:
                counter = CreateCountedEntryCounters(
                    counter_name="-1/-1", source_ref=subject.object_ref, placing_player=subject.destination_controller,
                    matching_objects=matching, simultaneous_departures=tuple(sorted(concurrent.intersection(matching))),
                )
                effect = replace(effect, operations=(*effect.operations, counter))
            prepared.append(effect)
        return tuple(prepared)


@dataclass(frozen=True, slots=True)
class LimitedCreatureTypeEntryDesignationHandler(EntryDesignationHandler):
    handler_id: str = LIMITED_CREATURE_TYPE_ENTRY_HANDLER_ID
    schema_version: int = 3
    capability_dependencies: tuple[str, ...] = (ENTRY_DESIGNATION_CAPABILITY, LIMITED_CREATURE_TYPE_ENTRY_CAPABILITY)

    def validate(self, descriptor):
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "designation", "options"},
                     field="limited creature-type entry designation")
        if (descriptor["handler_id"] != self.handler_id or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version or descriptor["event"] != self.event
                or descriptor["designation"] != "creature_type"):
            raise SemanticNodeError("Limited creature-type entry designation identity changed")
        options = descriptor["options"]
        if not isinstance(options, (list, tuple)) or len(options) < 2:
            raise SemanticNodeError("A limited creature-type choice requires a closed list")
        try:
            chosen = tuple(validate_designation(EntryDesignationKind.CREATURE_TYPE, value) for value in options)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        if chosen != tuple(sorted(set(chosen))):
            raise SemanticNodeError("Limited creature-type choices must be ordered and unique")
        return EntryDesignationKind.CREATURE_TYPE

    def _designation_values(self, descriptor, kind):
        return tuple(descriptor["options"])



@dataclass(frozen=True, slots=True)
class CountedCreatureTypeEntryDesignationHandler(EntryDesignationHandler):
    handler_id: str = COUNTED_CREATURE_TYPE_ENTRY_HANDLER_ID
    schema_version: int = 4
    capability_dependencies: tuple[str, ...] = (
        ENTRY_DESIGNATION_CAPABILITY, COUNTED_CREATURE_TYPE_ENTRY_CAPABILITY,
        "counter.placement.quantity_replacement",
    )
    rule_references: tuple[str, ...] = (
        "109.5", "122.6", "122.6a", "205.3m", "400.7", "614.1c", "614.12",
        "614.12a", "614.13", "616.1", "707.2", "707.5",
    )

    def validate(self, descriptor):
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "designation", "counter_name"},
                     field="counted creature-type entry designation")
        if descriptor["designation"] != "creature_type":
            raise SemanticNodeError("Counted entry counters require a creature-type choice")
        try:
            name = validate_counted_entry_counter_name(descriptor["counter_name"])
            if keyword_counter_mechanic(name) is not None:
                raise ValueError("Counted creature-type entry requires a nonkeyword counter")
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        return EntryDesignationHandler.validate(
            self, {key: value for key, value in descriptor.items() if key != "counter_name"},
        )

    def subject_replacement_effects(self, descriptor, *, subject, component_id, host=None,
                                    simultaneous_departures=()):
        kind = self.validate(descriptor)
        controller = subject.destination_controller
        if host is None or controller not in host.state.players or not component_id:
            raise SemanticNodeError("Counted entry designation requires its captured controller and component")
        matches = defaultdict(list)
        for object_id, subtypes in _controlled_entry_creatures(host, controller):
            for subtype in canonical_creature_types(subtypes):
                matches[subtype].append(object_id)

        concurrent = set(simultaneous_departures)
        effects = []
        for value in kind.values:
            matching = tuple(sorted(matches[value]))
            counters = (CreateCountedEntryCounters(
                counter_name=descriptor["counter_name"], source_ref=subject.object_ref, placing_player=controller,
                matching_objects=matching, simultaneous_departures=tuple(sorted(concurrent.intersection(matching))),
            ),) if matching else ()
            effects.append(self._designation_effect(subject, component_id, kind, value, followups=counters))
        return tuple(effects)



@dataclass(frozen=True, slots=True)
class SelfChosenSubtypeEntryHandler(EntryDesignationHandler):
    handler_id: str = SELF_CHOSEN_SUBTYPE_ENTRY_HANDLER_ID
    schema_version: int = 5
    rule_references: tuple[str, ...] = ("205.1b", "205.3d", "614.12", "614.12a", "616.1", "607.2d")
    capability_dependencies: tuple[str, ...] = (ENTRY_DESIGNATION_CAPABILITY, SELF_CHOSEN_SUBTYPE_CAPABILITY)

    def validate(self, descriptor):
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "designation", "requires_complete_card_program"},
                     field="self chosen subtype entry designation")
        if descriptor["requires_complete_card_program"] is not True or descriptor["designation"] != "creature_type":
            raise SemanticNodeError("Self chosen subtype entry requires the complete linked creature type")
        return EntryDesignationHandler.validate(
            self, {key: value for key, value in descriptor.items() if key != "requires_complete_card_program"},
        )

    def subject_replacement_effects(self, descriptor, *, subject, component_id):
        kind = self.validate(descriptor)
        if subject.destination_controller is None or not component_id:
            raise SemanticNodeError("Self chosen type entry requires its captured controller and component")
        return tuple(self._designation_effect(
            subject, component_id, kind, value, followups=(UnionValues("object_types", (value,)),),
        ) for value in kind.values)
