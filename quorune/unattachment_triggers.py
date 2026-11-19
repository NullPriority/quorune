from __future__ import annotations

"""Capture attachment trigger information at the relation's prior state."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .attachment_references import (
    AttachmentReferenceKind,
    SourceAttachmentSnapshot,
    capture_source_attachment_snapshot,
)
from .rules.event_subscriptions import event_has_capability_subscriber
from .source_sacrifice_model import UNATTACHMENT_EVENT, UNATTACHMENT_SNAPSHOT, UNATTACHMENT_TRIGGER_CAPABILITY
from .zone_trigger_processing import capture_departure_trigger_sources

if TYPE_CHECKING:
    from .model import StackItem
    from .zone_trigger_processing import DepartureTriggerSnapshot


@dataclass(frozen=True, slots=True)
class PreparedUnattachment:
    relation: SourceAttachmentSnapshot
    controller: str
    trigger_sources: DepartureTriggerSnapshot


def prepare_equipment_unattachment(host, source) -> PreparedUnattachment | None:
    if source.attached_to is None or not event_has_capability_subscriber(
        host, UNATTACHMENT_EVENT, UNATTACHMENT_TRIGGER_CAPABILITY,
    ):
        return None
    relation = capture_source_attachment_snapshot(host.state.cards, source, AttachmentReferenceKind.EQUIPPED)
    if relation.attached_object is None:
        return None
    sources = capture_departure_trigger_sources(host, semantic_events=True, origin="battlefield")
    return PreparedUnattachment(relation, source.controller, sources)


def dispatch_unattachment(host, prepared: PreparedUnattachment | None, *,
                          trigger_batch: list[StackItem] | None = None) -> None:
    if prepared is None:
        return
    from .trigger_discovery import dispatch_semantic_event

    relation = prepared.relation
    dispatch_semantic_event(
        host,
        UNATTACHMENT_EVENT,
        {
            "card": relation.source.reference,
            "object_id": relation.source.object_id,
            "logical_object_id": relation.source.logical_object_id,
            "source_logical_object_id": relation.source.logical_object_id,
            "controller": prepared.controller,
            UNATTACHMENT_SNAPSHOT: relation.to_dict(),
        },
        sources=prepared.trigger_sources.sources,
        source_zones=prepared.trigger_sources.source_zones,
        source_characteristics=prepared.trigger_sources.source_characteristics,
        trigger_batch=trigger_batch,
    )


def dispatch_departure_unattachments(host, occurrence, before: DepartureTriggerSnapshot, *,
                                    trigger_batch: list[StackItem]) -> None:
    if (occurrence.origin != "battlefield" or occurrence.destination == "battlefield"
            or not event_has_capability_subscriber(host, UNATTACHMENT_EVENT, UNATTACHMENT_TRIGGER_CAPABILITY)):
        return
    cards = {card.object_id: card for card in before.sources}
    departing = cards.get(occurrence.object_id)
    if departing is None or departing.logical_object_id != occurrence.previous_logical_object_id:
        return
    sources = (departing, *(cards[object_id] for object_id in departing.attachments if object_id in cards))
    for source in sources:
        relation = capture_source_attachment_snapshot(cards, source, AttachmentReferenceKind.EQUIPPED)
        if relation.attached_object is None:
            continue
        fact = relation.to_dict()
        if any(item.context.get(UNATTACHMENT_SNAPSHOT) == fact for item in trigger_batch):
            continue
        dispatch_unattachment(host, PreparedUnattachment(relation, source.controller, before),
                              trigger_batch=trigger_batch)


def prepare_player_departure_unattachments(host, seats) -> tuple[PreparedUnattachment, ...]:
    if not event_has_capability_subscriber(host, UNATTACHMENT_EVENT, UNATTACHMENT_TRIGGER_CAPABILITY):
        return ()
    before = capture_departure_trigger_sources(host, semantic_events=True, origin="battlefield")
    cards = {card.object_id: card for card in before.sources}
    prepared = []
    for source in before.sources:
        target = cards.get(source.attached_to)
        if target is None or source.controller in seats:
            continue
        if not {source.owner, target.owner, target.controller}.intersection(seats):
            continue
        relation = capture_source_attachment_snapshot(cards, source, AttachmentReferenceKind.EQUIPPED)
        if relation.attached_object is not None:
            prepared.append(PreparedUnattachment(relation, source.controller, before))
    return tuple(prepared)


def pending_unattachment_snapshots(state):
    return tuple(
        item.event_facts[UNATTACHMENT_SNAPSHOT]
        for batch in state.pending_trigger_batches
        for group in batch.groups
        for item in group.items
        if UNATTACHMENT_SNAPSHOT in item.event_facts
    )


def dispatch_player_departure_unattachments(host, prepared: tuple[PreparedUnattachment, ...]) -> None:
    if not prepared:
        return
    observed = pending_unattachment_snapshots(host.state)
    for event in prepared:
        relation = event.relation
        source = host.state.cards.get(relation.source.object_id)
        if (source is not None and source.logical_object_id == relation.source.logical_object_id
                and source.attached_to == relation.attached_object.object_id):
            continue
        if event.controller in host.active_seats and relation.to_dict() not in observed:
            dispatch_unattachment(host, event)
