from __future__ import annotations

from dataclasses import dataclass, field

from ..entry_keyword_grants import EntryKeywordGrant
from ..entry_counters import EffectEntryCounter, IntrinsicEntryCounter
from ..entry_state_conditions import FixedEntryMetric
from ..replacement.immutable import FrozenMap
from ..replacement_effects import (
    ReplaceableEvent,
    ReplacementBatchChoice,
    ReplacementEffect,
    ReplacementEventBatch,
    ReplacementSelection,
)
from ..zone_trigger_events import ZoneTransitionKind
_EXILE_ZONE = "exile"
_LIBRARY_ZONE = "library"
SUPPORTED_ZONE_DESTINATIONS = frozenset(
    {
        "battlefield",
        "command",
        _EXILE_ZONE,
        "graveyard",
        "hand",
        _LIBRARY_ZONE,
        "outside",
    }
)


class ZoneReplacementError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ZoneDestinationReplacementNode:
    destination: str
    object_kind: str
    owner_relation: str
    replacement_destination: str
    counters: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class ZoneChangeReplacementContext:
    source_ref: str
    source_controller: str
    object_id: str
    object_ref: str
    object_owner: str
    object_controller: str | None
    object_types: tuple[str, ...]
    origin: str
    destination: str
    is_card_object: bool
    component_id: str = ""

    def __post_init__(self) -> None:
        if not all(
            (
                self.source_ref,
                self.source_controller,
                self.object_id,
                self.object_ref,
                self.object_owner,
                self.origin,
                self.destination,
            )
        ):
            raise ZoneReplacementError(
                "Zone replacement context requires stable source and event facts"
            )


@dataclass(frozen=True, slots=True)
class ZoneDestinationIntent:
    handler_id: str
    source_ref: str
    destination: str
    counters: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class ZoneChangeReplacementResolution:
    batch: ReplacementEventBatch
    event: ReplaceableEvent
    effects: tuple[ReplacementEffect, ...]
    journal: tuple[ReplacementSelection, ...]
    pending: ReplacementBatchChoice | None

    @property
    def destination(self) -> str:
        return str(self.event.payload["destination"])

    @property
    def counter_events(self) -> tuple[ReplaceableEvent, ...]:
        events: list[ReplaceableEvent] = []

        def visit(event: ReplaceableEvent) -> None:
            if event.kind == "counter.place":
                events.append(event)
            for child in event.children:
                visit(child)

        visit(self.event)
        return tuple(events)


@dataclass(frozen=True, slots=True)
class ZoneChangeSubjectSnapshot:
    object_id: str
    object_ref: str
    logical_object_id: str
    owner: str
    controller: str | None
    origin: str
    destination: str
    destination_controller: str | None
    entry_face_id: str
    object_types: tuple[str, ...]
    is_card_object: bool
    transition_kind: ZoneTransitionKind = ZoneTransitionKind.ORDINARY
    is_commander: bool = False
    commander_designation_id: str | None = None
    requested_tapped: bool = False
    entry_pay_life: bool | None = None
    opponent_count: int = 0
    controller_basic_land_types: tuple[str, ...] = ()
    entry_condition_metrics: FrozenMap = field(default_factory=FrozenMap)
    opponent_was_dealt_damage_this_turn: bool = False
    mana_colors_spent: tuple[str, ...] = ()
    intrinsic_entry_counters: tuple[IntrinsicEntryCounter, ...] = ()
    effect_entry_counters: tuple[EffectEntryCounter, ...] = ()
    cast_option: str | None = None

    def __post_init__(self) -> None:
        required = (
            self.object_id,
            self.object_ref,
            self.logical_object_id,
            self.owner,
            self.origin,
            self.destination,
            self.entry_face_id,
        )
        if any(type(value) is not str or not value for value in required):
            raise ZoneReplacementError(
                "Zone replacement subjects require stable pre-move identity"
            )
        if self.controller == "" or self.destination_controller == "":
            raise ZoneReplacementError(
                "Zone replacement controllers cannot be empty"
            )
        if type(self.is_commander) is not bool:
            raise ZoneReplacementError(
                "Zone replacement commander state must be boolean"
            )
        if self.commander_designation_id == "":
            raise ZoneReplacementError(
                "Commander designation identity cannot be empty"
            )
        if self.commander_designation_id is not None and not self.is_commander:
            raise ZoneReplacementError(
                "Only a commander subject may carry a designation"
            )
        if self.destination not in SUPPORTED_ZONE_DESTINATIONS:
            raise ZoneReplacementError(
                "Zone replacement subjects require a supported destination"
            )
        object_types = tuple(sorted(set(self.object_types)))
        if any(type(value) is not str or not value for value in object_types):
            raise ZoneReplacementError(
                "Zone replacement subject types must be canonical strings"
            )
        object.__setattr__(self, "object_types", object_types)
        entry_counters = tuple(self.intrinsic_entry_counters)
        if any(
            not isinstance(value, IntrinsicEntryCounter)
            for value in entry_counters
        ):
            raise ZoneReplacementError(
                "Zone replacement entry counters must be typed instructions"
            )
        object.__setattr__(
            self, "intrinsic_entry_counters", entry_counters
        )
        effect_entry_counters = tuple(self.effect_entry_counters)
        if any(
            not isinstance(value, EffectEntryCounter)
            for value in effect_entry_counters
        ):
            raise ZoneReplacementError(
                "Zone replacement effect entry counters must be typed instructions"
            )
        object.__setattr__(
            self,
            "effect_entry_counters",
            effect_entry_counters,
        )
        if self.cast_option is not None and (
            type(self.cast_option) is not str or not self.cast_option
        ):
            raise ZoneReplacementError(
                "Zone replacement cast option must be nonempty or null"
            )
        if type(self.is_card_object) is not bool:
            raise ZoneReplacementError(
                "Zone replacement card-object state must be boolean"
            )
        if not isinstance(self.transition_kind, ZoneTransitionKind):
            raise ZoneReplacementError(
                "Zone replacement transition kind must be typed"
            )
        if type(self.requested_tapped) is not bool:
            raise ZoneReplacementError(
                "Zone replacement requested tapped state must be boolean"
            )
        if self.entry_pay_life is not None and (
            type(self.entry_pay_life) is not bool
        ):
            raise ZoneReplacementError(
                "Zone replacement entry life choice must be boolean or null"
            )
        if type(self.opponent_count) is not int or self.opponent_count < 0:
            raise ZoneReplacementError(
                "Zone replacement opponent count must be nonnegative"
            )
        basic_types = tuple(sorted(set(self.controller_basic_land_types)))
        if any(
            type(value) is not str or not value for value in basic_types
        ):
            raise ZoneReplacementError(
                "Zone replacement controlled basic land types must be strings"
            )
        object.__setattr__(
            self,
            "controller_basic_land_types",
            basic_types,
        )
        metrics = self.entry_condition_metrics
        if not isinstance(metrics, FrozenMap):
            try:
                metrics = FrozenMap(metrics)
            except (TypeError, ValueError) as exc:
                raise ZoneReplacementError(
                    "Zone replacement entry metrics must be an object"
                ) from exc
        allowed_entry_metrics = {metric.value for metric in FixedEntryMetric}
        if any(key not in allowed_entry_metrics for key in metrics):
            raise ZoneReplacementError(
                "Zone replacement entry metric keys are outside the closed "
                "vocabulary"
            )
        if any(
            type(value) is not int
            or (
                value < 0
                and key != FixedEntryMetric.MINIMUM_PLAYER_LIFE.value
            )
            for key, value in metrics.items()
        ):
            raise ZoneReplacementError(
                "Zone replacement entry metrics must be nonnegative integers"
            )
        object.__setattr__(self, "entry_condition_metrics", metrics)
        if type(self.opponent_was_dealt_damage_this_turn) is not bool:
            raise ZoneReplacementError(
                "Zone replacement turn-history facts must be boolean"
            )
        mana_colors = tuple(self.mana_colors_spent)
        if any(
            type(value) is not str or value not in "WUBRG"
            for value in mana_colors
        ) or len(mana_colors) != len(set(mana_colors)):
            raise ZoneReplacementError(
                "Zone replacement cast colors must be distinct WUBRG symbols"
            )
        object.__setattr__(
            self,
            "mana_colors_spent",
            tuple(color for color in "WUBRG" if color in mana_colors),
        )

    @property
    def chooser(self) -> str:
        return self.controller or self.owner


@dataclass(frozen=True, slots=True)
class ZoneChangeReplacementSnapshot:
    revision: int
    event_sequence: int
    apnap_order: tuple[str, ...]
    source_refs: tuple[str, ...]
    subjects: tuple[ZoneChangeSubjectSnapshot, ...]
    effects: tuple[ReplacementEffect, ...]

    def __post_init__(self) -> None:
        if type(self.revision) is not int or self.revision < 0:
            raise ZoneReplacementError(
                "Zone replacement snapshots require a nonnegative revision"
            )
        if type(self.event_sequence) is not int or self.event_sequence < 0:
            raise ZoneReplacementError(
                "Zone replacement snapshots require a nonnegative event sequence"
            )
        order = tuple(self.apnap_order)
        if (
            not order
            or any(not value for value in order)
            or len(order) != len(set(order))
        ):
            raise ZoneReplacementError(
                "Zone replacement snapshots require a complete APNAP order"
            )
        source_refs = tuple(sorted(set(self.source_refs)))
        if any(type(value) is not str or not value for value in source_refs):
            raise ZoneReplacementError(
                "Zone replacement snapshots require stable source refs"
            )
        subjects = tuple(self.subjects)
        object_ids = [subject.object_id for subject in subjects]
        if not subjects or len(object_ids) != len(set(object_ids)):
            raise ZoneReplacementError(
                "Zone replacement snapshots require unique affected objects"
            )
        effects = tuple(self.effects)
        if any(not isinstance(effect, ReplacementEffect) for effect in effects):
            raise ZoneReplacementError(
                "Zone replacement snapshots require typed effects"
            )
        effect_ids = [effect.effect_id for effect in effects]
        if len(effect_ids) != len(set(effect_ids)):
            raise ZoneReplacementError(
                "Zone replacement snapshot effect IDs must be unique"
            )
        object.__setattr__(self, "apnap_order", order)
        object.__setattr__(self, "source_refs", source_refs)
        object.__setattr__(self, "subjects", subjects)
        object.__setattr__(self, "effects", effects)


@dataclass(frozen=True, slots=True)
class PreparedZoneChange:
    object_id: str
    logical_object_id: str
    origin: str
    requested_destination: str
    destination_controller: str | None
    entry_face_id: str
    state_revision: int
    event_sequence: int
    destination: str
    transition_kind: ZoneTransitionKind = ZoneTransitionKind.ORDINARY
    requested_tapped: bool = False
    requested_entry_pay_life: bool | None = None
    entry_tapped: bool = False
    entry_life_payment: int = 0
    read_ahead_chapter: int | None = None
    event: ReplaceableEvent | None = None
    effects: tuple[ReplacementEffect, ...] = ()
    counter_events: tuple[ReplaceableEvent, ...] = ()
    keyword_grants: tuple[EntryKeywordGrant, ...] = ()
    journal: tuple[ReplacementSelection, ...] = ()

    def __post_init__(self) -> None:
        required = (
            self.object_id,
            self.logical_object_id,
            self.origin,
            self.requested_destination,
            self.entry_face_id,
            self.destination,
        )
        if any(type(value) is not str or not value for value in required):
            raise ZoneReplacementError(
                "Prepared zone changes require an exact transition identity"
            )
        if self.destination_controller == "":
            raise ZoneReplacementError(
                "Prepared zone-change controllers cannot be empty"
            )
        if not isinstance(self.transition_kind, ZoneTransitionKind):
            raise ZoneReplacementError(
                "Prepared zone-change transition kind must be typed"
            )
        if (
            type(self.state_revision) is not int
            or self.state_revision < 0
            or type(self.event_sequence) is not int
            or self.event_sequence < 0
        ):
            raise ZoneReplacementError(
                "Prepared zone changes require nonnegative state coordinates"
            )
        if self.requested_destination not in SUPPORTED_ZONE_DESTINATIONS or (
            self.destination not in SUPPORTED_ZONE_DESTINATIONS
        ):
            raise ZoneReplacementError(
                "Prepared zone changes require supported destinations"
            )
        if type(self.requested_tapped) is not bool:
            raise ZoneReplacementError(
                "Prepared requested tapped state must be boolean"
            )
        if self.requested_entry_pay_life is not None and (
            type(self.requested_entry_pay_life) is not bool
        ):
            raise ZoneReplacementError(
                "Prepared entry life choice must be boolean or null"
            )
        if type(self.entry_tapped) is not bool:
            raise ZoneReplacementError(
                "Prepared entry tapped state must be boolean"
            )
        if type(self.entry_life_payment) is not int or (
            self.entry_life_payment < 0
        ):
            raise ZoneReplacementError(
                "Prepared entry life payment must be nonnegative"
            )
        if self.read_ahead_chapter is not None and (
            type(self.read_ahead_chapter) is not int
            or self.read_ahead_chapter < 1
        ):
            raise ZoneReplacementError(
                "Prepared Read Ahead chapter must be positive or null"
            )
        if not isinstance(self.event, ReplaceableEvent):
            raise ZoneReplacementError(
                "Prepared zone changes require a typed resolved event"
            )
        for field_name, values, value_type in (
            ("effects", tuple(self.effects), ReplacementEffect),
            ("counter events", tuple(self.counter_events), ReplaceableEvent),
            ("keyword grants", tuple(self.keyword_grants), EntryKeywordGrant),
            ("journal", tuple(self.journal), ReplacementSelection),
        ):
            if any(not isinstance(value, value_type) for value in values):
                raise ZoneReplacementError(
                    f"Prepared zone-change {field_name} must be typed"
                )
