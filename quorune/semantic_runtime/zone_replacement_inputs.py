from __future__ import annotations

"""Closed input validation for immutable zone-replacement snapshots."""

from typing import Any, Mapping, Sequence

from ..entry_counters import EffectEntryCounter
from ..zone_trigger_events import ZoneTransitionKind
from ..replacement_effects import AffectedObject, ReplaceableEvent
from .zone_replacement_model import ZoneChangeReplacementSnapshot, ZoneChangeSubjectSnapshot


def zone_change_subject_card(
    host: Any,
    object_id: str,
    prospective_objects: Mapping[str, Any],
    error_type: type[Exception],
) -> Any:
    """Resolve an existing or explicitly prospective immutable subject."""
    card = prospective_objects.get(object_id) or host.state.cards.get(object_id)
    if card is None:
        raise error_type("Zone replacement snapshot references an unknown object")
    return card


def active_zone_replacement_sources(
    host: Any,
    *,
    sources: Sequence[Any] | None,
    source_zones: Mapping[str, str] | None,
) -> tuple[Any, ...]:
    candidates = (
        tuple(sources)
        if sources is not None
        else tuple(host._semantic_event_sources(zones={"battlefield"}))
    )
    return tuple(
        source
        for source in candidates
        if (
            (
                source_zones.get(source.object_id, source.zone)
                if source_zones is not None
                else source.zone
            )
            == "battlefield"
            and not source.phased_out
            and source.controller in host.active_seats
        )
    )


def prospective_destination_controller(
    card: Any,
    destination_controllers: Mapping[str, str | None],
) -> str | None:
    return destination_controllers.get(
        card.object_id,
        card.controller if card.zone == "stack" else card.owner,
    )


def validated_zone_change_snapshot_inputs(
    host: Any,
    changes: Sequence[tuple[str, str]],
    *,
    destination_controllers: Mapping[str, str | None] | None,
    entry_characteristics: Mapping[str, Mapping[str, Any]] | None,
    effect_entry_counters: Mapping[
        str, Sequence[EffectEntryCounter]
    ] | None,
    mana_colors_spent: Mapping[str, Sequence[str]] | None,
    requested_tapped: Mapping[str, bool] | None,
    entry_pay_life: Mapping[str, bool | None] | None,
    transition_kinds: Mapping[str, ZoneTransitionKind] | None,
    error_type: type[Exception],
) -> tuple[
    tuple[tuple[str, str], ...],
    Mapping[str, str | None],
    Mapping[str, Mapping[str, Any]],
    Mapping[str, Sequence[EffectEntryCounter]],
    Mapping[str, Sequence[str]],
    Mapping[str, bool],
    Mapping[str, bool | None],
    Mapping[str, ZoneTransitionKind],
]:
    supplied = tuple(changes)
    if any(
        not isinstance(change, tuple)
        or len(change) != 2
        or any(type(value) is not str or not value for value in change)
        for change in supplied
    ):
        raise error_type(
            "Zone replacement snapshots require object and destination pairs"
        )
    object_ids = tuple(object_id for object_id, _destination in supplied)
    if len(object_ids) != len(set(object_ids)):
        raise error_type("Zone replacement snapshots cannot repeat one object")

    controllers = destination_controllers or {}
    characteristics = entry_characteristics or {}
    effect_counters = effect_entry_counters or {}
    cast_colors = mana_colors_spent or {}
    tapped_requests = requested_tapped or {}
    life_choices = entry_pay_life or {}
    kinds = transition_kinds or {}
    keyed_inputs = (
        (controllers, "destination controllers"),
        (characteristics, "entry characteristics"),
        (effect_counters, "effect entry counters"),
        (cast_colors, "cast colors"),
        (tapped_requests, "tapped requests"),
        (life_choices, "entry life choices"),
        (kinds, "transition kinds"),
    )
    for values, label in keyed_inputs:
        if set(values) - set(object_ids):
            raise error_type(
                f"Zone replacement {label} reference unknown objects"
            )
    if any(not isinstance(value, Mapping) for value in characteristics.values()):
        raise error_type(
            "Zone replacement entry characteristics must be mappings"
        )
    if any(
        not isinstance(values, (list, tuple))
        or any(type(value) is not str or value not in "WUBRG" for value in values)
        or len(values) != len(set(values))
        for values in cast_colors.values()
    ):
        raise error_type(
            "Zone replacement cast colors must be distinct WUBRG sequences"
        )
    if any(type(value) is not bool for value in tapped_requests.values()):
        raise error_type("Zone replacement tapped requests must be booleans")
    if any(
        value is not None and type(value) is not bool
        for value in life_choices.values()
    ):
        raise error_type(
            "Zone replacement entry life choices must be booleans or null"
        )
    if any(
        not isinstance(value, ZoneTransitionKind) for value in kinds.values()
    ):
        raise error_type("Zone replacement transition kinds must be typed")
    if any(
        not isinstance(values, (list, tuple))
        or any(not isinstance(value, EffectEntryCounter) for value in values)
        for values in effect_counters.values()
    ):
        raise error_type(
            "Zone replacement effect entry counters must be typed sequences"
        )
    if any(
        counter.placing_player not in host.active_seats
        for values in effect_counters.values()
        for counter in values
    ):
        raise error_type(
            "Zone replacement effect entry counter player is not active"
        )
    return (
        supplied,
        controllers,
        characteristics,
        effect_counters,
        cast_colors,
        tapped_requests,
        life_choices,
        kinds,
    )


def zone_change_snapshot_event(
    snapshot: ZoneChangeReplacementSnapshot,
    subject: ZoneChangeSubjectSnapshot,
) -> ReplaceableEvent:
    return ReplaceableEvent(
        event_id=(
            f"zone.change:{snapshot.revision}:"
            f"{snapshot.event_sequence + 1}:{subject.object_ref}"
        ),
        kind="zone.change",
        affected_player=None,
        affected_object=AffectedObject(
            object_id=subject.object_id,
            owner=subject.owner,
            controller=(
                subject.owner
                if subject.is_commander
                and subject.destination in {"hand", "library"}
                else (
                    subject.destination_controller
                    if subject.destination == "battlefield"
                    else subject.controller
                )
            ),
        ),
        payload={
            "origin": subject.origin,
            "destination": subject.destination,
            "destination_controller": subject.destination_controller,
            "object_kind": "card" if subject.is_card_object else "noncard",
            "object_ref": subject.object_ref,
            "object_types": list(subject.object_types),
            "logical_object_id": subject.logical_object_id,
            **({"prospective_subject": True} if subject.prospective_subject else {}),
            "transition_kind": subject.transition_kind.value,
            "owner": subject.owner,
            **(
                {"cast_option": subject.cast_option}
                if subject.cast_option is not None
                else {}
            ),
            "tapped": subject.requested_tapped,
            "entry_life_payment": 0,
            "read_ahead_chapter": None,
            "opponent_count": subject.opponent_count,
            "controller_basic_land_types": list(
                subject.controller_basic_land_types
            ),
            "opponent_was_dealt_damage_this_turn": (
                subject.opponent_was_dealt_damage_this_turn
            ),
        },
    )


__all__ = [
    "active_zone_replacement_sources",
    "prospective_destination_controller",
    "validated_zone_change_snapshot_inputs",
    "zone_change_snapshot_event",
    "zone_change_subject_card",
]
