from __future__ import annotations

import copy
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .model import CardInstance, StackItem
from .creature_power_damage_model import OCCURRENCE_POWER_DAMAGE_CONTEXT
from .saga_progression import dispatch_saga_entry_chapters
from .shared_creature_type_model import canonical_creature_types
from .trigger_processing import enqueue_trigger_batch
from .zone_trigger_events import (
    ZoneChangeOccurrence,
    ZoneTransitionKind,
    normalized_zone_trigger_events,
    sealed_public_characteristic_facts,
)


_EXILE_ZONE = "exile"
_DEFAULT_SEMANTIC_SOURCE_ZONES = frozenset(
    {"battlefield", "graveyard", _EXILE_ZONE, "command", "hand"}
)


class ZoneTriggerProcessingHost(Protocol):
    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> Sequence[CardInstance]: ...

    def _effective_card_data(
        self, card: CardInstance
    ) -> Mapping[str, Any]: ...

    def _dispatch_semantic_event(
        self,
        event: str,
        context: Mapping[str, Any],
        *,
        sources: Sequence[CardInstance] | None = None,
        source_zones: Mapping[str, str] | None = None,
        source_characteristics: Mapping[
            str, Mapping[str, Any]
        ] | None = None,
        trigger_batch: list[StackItem] | None = None,
    ) -> list[str]: ...

    def _record_turn_history(
        self,
        kind: str,
        *,
        actor: str,
        object_incarnation: str,
        types: set[str],
        target: str | None = None,
        target_kind: str | None = None,
        origin: str | None = None,
        creature_subtypes: tuple[str, ...] | None = None,
        token: bool | None = None,
    ) -> None: ...

@dataclass(frozen=True, slots=True)
class DepartureTriggerSnapshot:
    sources: tuple[CardInstance, ...] = ()
    source_zones: Mapping[str, str] = field(default_factory=dict)
    source_characteristics: Mapping[
        str, Mapping[str, Any]
    ] = field(default_factory=dict)


def semantic_event_sources(
    cards: Iterable[CardInstance],
    *,
    active_seats: Collection[str],
    zones: set[str] | None = None,
) -> list[CardInstance]:
    """Select zone-visible sources without coupling discovery to the engine."""

    active_zones = zones or _DEFAULT_SEMANTIC_SOURCE_ZONES
    return [
        card
        for card in cards
        if card.zone in active_zones
        and (card.controller in active_seats or card.owner in active_seats)
    ]


def capture_departure_trigger_sources(
    host: ZoneTriggerProcessingHost,
    *,
    semantic_events: bool,
    origin: str,
) -> DepartureTriggerSnapshot:
    """Snapshot battlefield trigger sources and LKI before zone mutation."""

    if not semantic_events or origin not in {"battlefield", "stack"}:
        return DepartureTriggerSnapshot((), {}, {})
    source_zones = None
    if origin == "stack":
        source_zones = {
            "battlefield",
            "graveyard",
            _EXILE_ZONE,
            "command",
            "hand",
            "stack",
        }
    sources = tuple(
        copy.deepcopy(source)
        for source in host._semantic_event_sources(zones=source_zones)
    )
    return DepartureTriggerSnapshot(
        sources=sources,
        source_zones={source.object_id: source.zone for source in sources},
        source_characteristics={
            source.object_id: _damage_source_characteristics(host, source)
            for source in sources
        },
    )


def _damage_source_characteristics(host, source):
    from .rules.stack_damage_sources import capture_damage_source_characteristics
    from .rules.permanent_event_state import permanent_event_characteristics
    return permanent_event_characteristics(host,source,capture_damage_source_characteristics(host, source))


def dispatch_zone_change_occurrence(
    host: ZoneTriggerProcessingHost,
    occurrence: ZoneChangeOccurrence,
    card: CardInstance,
    *,
    departure_sources: Sequence[CardInstance],
    departure_source_zones: Mapping[str, str],
    departure_source_characteristics: Mapping[
        str, Mapping[str, Any]
    ],
    trigger_batch: list[StackItem] | None = None,
    history_pre_recorded: bool = False,
    composition_departures: Sequence[ZoneChangeOccurrence] = (),
) -> None:
    """Detect represented events from immutable facts, then use CR 603.3."""

    owns_trigger_batch = trigger_batch is None
    pending = trigger_batch if trigger_batch is not None else []
    events = normalized_zone_trigger_events(occurrence)
    if not history_pre_recorded:
        record_zone_change_history(host, occurrence, events=events)
    for event in events:
        context = event.context
        if event.kind == "permanent.enter" and getattr(card, "face_down", False):
            from .face_up_effect_model import FACE_DOWN_ENTRY_CAPABILITY, FACE_DOWN_ENTRY_FIELD
            from .rules.event_subscriptions import event_has_capability_subscriber

            if event_has_capability_subscriber(host, event.kind, FACE_DOWN_ENTRY_CAPABILITY):
                context = {**dict(context), FACE_DOWN_ENTRY_FIELD: True}
        if event.source_timing == "before":
            characteristics = departure_source_characteristics
            if event.kind == "creature.dies":
                snapshot = occurrence.previous_characteristics.get(OCCURRENCE_POWER_DAMAGE_CONTEXT)
                if snapshot is not None:
                    characteristics = {
                        **characteristics,
                        occurrence.object_id: {
                            **characteristics.get(occurrence.object_id, {}),
                            OCCURRENCE_POWER_DAMAGE_CONTEXT: snapshot,
                        },
                    }
            host._dispatch_semantic_event(
                event.kind,
                context,
                sources=departure_sources,
                source_zones=departure_source_zones,
                source_characteristics=characteristics,
                trigger_batch=pending,
            )
        else:
            host._dispatch_semantic_event(
                event.kind,
                context,
                trigger_batch=pending,
            )
    if occurrence.origin == "battlefield" and occurrence.destination in {"graveyard", "exile"}:
        from .earthbend import collect_earthbend_departure

        collect_earthbend_departure(host, occurrence, composition_departures, pending)
    if any(
        event.kind == "permanent.enter"
        and "saga" in event.context.get("subtypes", ())
        for event in events
    ):
        dispatch_saga_entry_chapters(
            host,
            card,
            read_ahead_chapter=occurrence.read_ahead_chapter,
            trigger_batch=pending,
        )
    if occurrence.origin == "stack" and occurrence.destination == "battlefield" and card.shared_type_doors is not None:
        from .rules.room_unlock import collect_room_unlock_events
        from .shared_type_doors import SharedTypeDoors

        after = card.shared_type_doors
        collect_room_unlock_events(
            host, card, SharedTypeDoors(after.faces), after, actor=card.controller, trigger_batch=pending,
        )
    if owns_trigger_batch:
        enqueue_trigger_batch(host, pending)


def record_zone_change_history(
    host: ZoneTriggerProcessingHost,
    occurrence: ZoneChangeOccurrence,
    *,
    events: Sequence[Any] | None = None,
) -> None:
    """Record one committed occurrence without discovering its triggers."""

    history=getattr(host.state,'turn_history',None)
    permanent=occurrence.composition is None or occurrence.composition.permanent_departure
    physical=occurrence.composition is None or occurrence.composition.physical_arrival
    if physical and occurrence.destination=='graveyard' and occurrence.origin!='graveyard' and occurrence.card_object and getattr(history,'arrival_history_version',None)==1:
        types=set(sealed_public_characteristic_facts(occurrence.current_characteristics)['types']) if history.graveyard_type_history_version==1 else set()
        host._record_turn_history('card_arrived_graveyard',actor=occurrence.owner,
            object_incarnation=occurrence.current_logical_object_id,types=types,origin=occurrence.origin)
    normalized = tuple(events or normalized_zone_trigger_events(occurrence))
    if getattr(history, "permanent_death_history_version", None) == 1:
        for event in normalized:
            if event.kind == "permanent.graveyard":
                host._record_turn_history(
                    "permanent_died",
                    actor=occurrence.previous_controller,
                    object_incarnation=occurrence.previous_logical_object_id,
                    types=set(event.context["types"]),
                    creature_subtypes=canonical_creature_types(event.context["subtypes"]),
                    token=occurrence.token,
                )
    previous_types = set(
        str(value)
        for event in normalized
        if event.kind == "permanent.leave"
        for value in event.context.get("types", ())
    )
    current_types = set(
        str(value)
        for event in normalized
        if event.kind == "permanent.enter"
        for value in event.context.get("types", ())
    )
    if any(event.kind == "permanent.enter" for event in normalized):
        host._record_turn_history(
            "permanent_entered",
            actor=occurrence.current_controller,
            object_incarnation=(
                f"{occurrence.object_id}@{occurrence.zone_change_counter}"
            ),
            types=current_types,
        )
    if (
        occurrence.origin == "battlefield"
        and occurrence.destination == "graveyard"
        and "creature" in previous_types
    ):
        host._record_turn_history(
            "creature_died",
            actor=occurrence.previous_controller,
            object_incarnation=occurrence.previous_logical_object_id,
            types=previous_types,
        )
    moved = occurrence.origin != occurrence.destination
    history = getattr(getattr(host, "state", None), "turn_history", None)
    movement_history = getattr(history, "public_movement_history_version", None) == 1
    if movement_history and moved:
        if physical and occurrence.destination == "exile" and occurrence.card_object:
            host._record_turn_history(
                "card_arrived_exile", actor=occurrence.owner,
                object_incarnation=occurrence.current_logical_object_id, types=set(),
            )
        if (permanent and previous_types and occurrence.origin == "battlefield"
                and occurrence.destination in {"hand", "graveyard"}):
            kind = "permanent_arrived_hand" if occurrence.destination == "hand" else "permanent_arrived_graveyard"
            host._record_turn_history(
                kind, actor=occurrence.owner, object_incarnation=occurrence.previous_logical_object_id,
                types=previous_types,
            )
    if (
        permanent and moved and occurrence.origin == "battlefield"
        and getattr(history, "departure_history_version", None) == 1
    ):
        host._record_turn_history(
            "permanent_left", actor=occurrence.previous_controller,
            object_incarnation=occurrence.previous_logical_object_id,
            types=previous_types,
        )
    if (
        moved
        and occurrence.origin == "hand"
        and occurrence.transition_kind is ZoneTransitionKind.DISCARD
    ):
        host._record_turn_history(
            "card_discarded",
            actor=occurrence.previous_controller,
            object_incarnation=occurrence.previous_logical_object_id,
        )
    if (
        moved
        and occurrence.origin == "battlefield"
        and occurrence.transition_kind is ZoneTransitionKind.SACRIFICE
        and permanent
    ):
        host._record_turn_history(
            "permanent_sacrificed",
            actor=occurrence.previous_controller,
            object_incarnation=occurrence.previous_logical_object_id,
            types=previous_types,
        )
        if movement_history and "artifact" in previous_types:
            from .type_subtype_vocabulary import ARTIFACT_SUBTYPES

            subtypes = {value for event in normalized if event.kind == "permanent.leave"
                        for value in event.context.get("subtypes", ())}
            for subtype in sorted(subtypes & ARTIFACT_SUBTYPES):
                host._record_turn_history(
                    "artifact_subtype_sacrificed", actor=occurrence.previous_controller,
                    object_incarnation=occurrence.previous_logical_object_id, types=set(),
                    target=subtype, target_kind="artifact_subtype",
                )


__all__ = [
    "ZoneTriggerProcessingHost",
    "DepartureTriggerSnapshot",
    "capture_departure_trigger_sources",
    "dispatch_zone_change_occurrence",
    "record_zone_change_history",
    "semantic_event_sources",
]
