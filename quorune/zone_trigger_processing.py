from __future__ import annotations
from .death_history_names import occurrence_death_name_fields

import copy
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .model import CardInstance, StackItem
from .discard_effect_origin import OPPONENT_SOURCE_DISCARD_EVENT, opponent_discard_origin
from .creature_power_damage_model import OCCURRENCE_POWER_DAMAGE_CONTEXT
from .saga_progression import dispatch_saga_entry_chapters
from .shared_creature_type_model import canonical_creature_types
from .trigger_processing import enqueue_trigger_batch
from .rules.event_subscriptions import (
    GRAVEYARD_DEPARTURE_LOOKBACK_CAPABILITY, GRAVEYARD_DEPARTURE_LOOKBACK_EVENT, event_has_capability_subscriber,
)
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
        rules_names: tuple[str, ...] | None = None,
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


def order_observing_trigger_sources(host, sources, source_zones):
    """Order the represented source snapshots that were present for this occurrence."""
    from .phasing_model import PHASING_TRIGGER_SOURCE_VISIBILITY_VERSION

    candidates = sources if sources is not None else host._semantic_event_sources()
    zones = source_zones or {}
    if getattr(host.state, "phasing_lifecycle_version", None) == PHASING_TRIGGER_SOURCE_VISIBILITY_VERSION:
        candidates = [
            source for source in candidates
            if zones.get(source.object_id, source.zone) != "battlefield" or not source.phased_out
        ]
    return sorted(candidates, key=lambda source: (source.zone_timestamp, source.object_id, source.logical_object_id))


def capture_departure_trigger_sources(
    host: ZoneTriggerProcessingHost,
    *,
    semantic_events: bool,
    origin: str,
) -> DepartureTriggerSnapshot:
    """Snapshot battlefield trigger sources and LKI before zone mutation."""

    if not semantic_events or (origin not in {"battlefield", "stack"} and not (
        origin == "graveyard" and _has_graveyard_departure_lookback(host)
    )):
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


def _has_graveyard_departure_lookback(host):
    return event_has_capability_subscriber(
        host, GRAVEYARD_DEPARTURE_LOOKBACK_EVENT, GRAVEYARD_DEPARTURE_LOOKBACK_CAPABILITY,
    )


def _dispatch_graveyard_departure_lookback(host, occurrence, context, departure, pending):
    if occurrence.card_object and not occurrence.token and _has_graveyard_departure_lookback(host):
        host._dispatch_semantic_event(
            GRAVEYARD_DEPARTURE_LOOKBACK_EVENT, context,
            sources=departure.sources, source_zones=departure.source_zones,
            source_characteristics=departure.source_characteristics, trigger_batch=pending,
        )


def _damage_source_characteristics(host, source):
    from .rules.stack_damage_sources import capture_damage_source_characteristics
    from .rules.permanent_event_state import permanent_event_characteristics
    return permanent_event_characteristics(host,source,capture_damage_source_characteristics(host, source))




def dispatch_committed_batch_zone_events(host, snapshots, occurrences, *, trigger_batch):
    """Dispatch each committed batch member against its sealed departure inputs."""
    for (card, departure), occurrence in zip(snapshots, occurrences, strict=True):
        sources = departure.trigger_sources
        host._dispatch_zone_change_events(
            card,
            origin=departure.origin,
            destination=card.zone,
            origin_controller=departure.controller,
            origin_logical_object_id=departure.logical_object_id,
            origin_data=departure.characteristics,
            origin_counters=departure.counters,
            origin_attachments=departure.attachments,
            origin_attached_to=departure.attached_to,
            cast_option=departure.cast_option,
            paid_entry_copy=departure.paid_entry_copy,
            multikicker_paid=departure.multikicker_paid,
            declared_cast_costs=departure.declared_cast_costs if card.zone == "battlefield" else None,
            departure_sources=sources.sources,
            departure_source_zones=sources.source_zones,
            departure_source_characteristics=sources.source_characteristics,
            reason=occurrence.cause,
            transition_kind=occurrence.transition_kind,
            read_ahead_chapter=occurrence.read_ahead_chapter,
            trigger_batch=trigger_batch,
            history_pre_recorded=True,
            **({"putting_player": occurrence.putting_player} if occurrence.putting_player is not None else {}),
            **({"discard_effect_origin": occurrence.discard_effect_origin} if occurrence.discard_effect_origin is not None else {}),
        )


def _dispatch_opponent_source_discard(host, card, occurrence, context, trigger_batch):
    """Observe the same face-up discarded object in the zone reached by the effect."""
    if occurrence.discard_effect_origin is None:
        return
    from .zone_transition_model import PUBLIC_ZONES

    if (card.zone not in PUBLIC_ZONES or card.face_down or card.zone != occurrence.destination
            or card.logical_object_id != occurrence.current_logical_object_id
            or occurrence.owner not in host.active_seats):
        return
    causer = opponent_discard_origin(
        occurrence.discard_effect_origin.to_dict(), discarding_player=occurrence.owner,
        opponents=host.state.players,
    )
    if causer is None:
        return
    host._dispatch_semantic_event(
        OPPONENT_SOURCE_DISCARD_EVENT,
        {**context, "previous_controller": occurrence.owner, "causing_player": causer.controller,
         "discarded_logical_object_id": occurrence.current_logical_object_id},
        sources=(card,), trigger_batch=trigger_batch,
    )


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
    from .unattachment_triggers import dispatch_departure_unattachments
    departure = DepartureTriggerSnapshot(
        tuple(departure_sources), departure_source_zones, departure_source_characteristics,
    )
    dispatch_departure_unattachments(host, occurrence, departure, trigger_batch=pending)
    for event in events:
        context = event.context
        if event.kind == "card.leave_graveyard":
            _dispatch_graveyard_departure_lookback(host, occurrence, context, departure, pending)
        if event.kind == "card.discarded":
            _dispatch_opponent_source_discard(host, card, occurrence, context, pending)
        if event.kind=='permanent.enter':
            from .tribute_entry_results import tribute_entry_context
            from .devour import devour_entry_context
            context = {**dict(context), **tribute_entry_context(card, occurrence),
                       **devour_entry_context(card, occurrence.current_logical_object_id)}
        if event.kind == "creature.dies":
            from .event_card_return import death_source_exile_context

            context = death_source_exile_context(host, context, occurrence, composition_departures)
        if composition_departures and event.kind == "permanent.leave":
            from .event_card_return import event_library_departure_context

            context = event_library_departure_context(host, context, occurrence, composition_departures)
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
    if occurrence.destination == "graveyard":
        from .event_card_return import dispatch_graveyard_source_arrival

        dispatch_graveyard_source_arrival(host, occurrence, card, trigger_batch=pending)
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
                    **occurrence_death_name_fields(history, occurrence.previous_characteristics),
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
