from __future__ import annotations

"""Build immutable resolved zone plans from the shared replacement event tree."""

from typing import Mapping
from ..entry_keyword_grants import EntryKeywordGrant
from ..replacement import ReplaceableEvent,ReplacementEffect,ReplacementSelection
from .zone_replacement_model import ZoneChangeSubjectSnapshot,PreparedZoneChange,ZoneReplacementError

def prepared_from_zone_event(
    subject: ZoneChangeSubjectSnapshot,
    event: ReplaceableEvent,
    *,
    state_revision: int,
    event_sequence: int,
    effects: tuple[ReplacementEffect, ...],
    journal: tuple[ReplacementSelection, ...],
    devour_sacrifices: tuple[PreparedZoneChange,...] = (),
) -> PreparedZoneChange:
    counter_events: list[ReplaceableEvent] = []

    def visit(current: ReplaceableEvent) -> None:
        if current.kind == "counter.place":
            counter_events.append(current)
        for child in current.children:
            visit(child)

    visit(event)
    raw_grants = event.payload.get("entry_keyword_grants", ())
    if not isinstance(raw_grants, (list, tuple)):
        raise ZoneReplacementError("Entry keyword grants must be an array")
    keyword_grants: list[EntryKeywordGrant] = []
    for value in raw_grants:
        if not isinstance(value, Mapping) or set(value) != {
            "effect_id",
            "keyword",
            "sequence",
        }:
            raise ZoneReplacementError(
                "Entry keyword grants require exact typed fields"
            )
        keyword_grants.append(
            EntryKeywordGrant(
                effect_id=value["effect_id"],
                keyword=value["keyword"],
                sequence=value["sequence"],
            )
        )
    entry_tapped = event.payload.get("tapped")
    entry_life_payment = event.payload.get("entry_life_payment")
    read_ahead_chapter = event.payload.get("read_ahead_chapter")
    if type(entry_tapped) is not bool:
        raise ZoneReplacementError(
            "Prepared zone entry tapped state must be boolean"
        )
    if (
        type(entry_life_payment) is not int
        or entry_life_payment < 0
    ):
        raise ZoneReplacementError(
            "Prepared zone entry life payment must be nonnegative"
        )
    if read_ahead_chapter is not None and (
        type(read_ahead_chapter) is not int or read_ahead_chapter < 1
    ):
        raise ZoneReplacementError(
            "Prepared Read Ahead chapter must be a positive integer or null"
        )
    return PreparedZoneChange(
        object_id=subject.object_id,
        logical_object_id=subject.logical_object_id,
        origin=subject.origin,
        requested_destination=subject.destination,
        destination_controller=subject.destination_controller,
        entry_face_id=subject.entry_face_id,
        state_revision=state_revision,
        event_sequence=event_sequence,
        destination=str(event.payload["destination"]),
        transition_kind=subject.transition_kind,
        requested_tapped=subject.requested_tapped,
        requested_entry_pay_life=subject.entry_pay_life,
        entry_tapped=entry_tapped,
        entry_life_payment=entry_life_payment,
        read_ahead_chapter=read_ahead_chapter,
        entry_chosen_color=event.payload.get("entry_chosen_color"),
        entry_chosen_creature_type=event.payload.get("entry_chosen_creature_type"),
        event=event,
        effects=effects,
        counter_events=tuple(counter_events),
        keyword_grants=tuple(sorted(keyword_grants)),
        journal=journal,
        devour_sacrifices=devour_sacrifices,
    )
