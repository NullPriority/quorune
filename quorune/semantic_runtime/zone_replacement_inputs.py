from __future__ import annotations

"""Closed input validation for immutable zone-replacement snapshots."""

from typing import Any, Mapping, Sequence

from ..entry_counters import EffectEntryCounter
from ..zone_trigger_events import ZoneTransitionKind


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


__all__ = ["validated_zone_change_snapshot_inputs"]
