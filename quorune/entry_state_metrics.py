from __future__ import annotations

"""Cycle-safe public battlefield facts for fixed entry conditions."""

from typing import Any, Mapping, Protocol, Sequence

from .characteristic_read_frames import characteristic_read_method
from .entry_state_conditions import FixedEntryMetric
from .landwalk import BASIC_LAND_TYPES


class EntryConditionMetricHost(Protocol):
    state: Any

    @property
    def active_seats(self) -> Sequence[str]: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self,
        type_line: str,
    ) -> tuple[set[str], set[str], set[str]]: ...


@characteristic_read_method
def controller_basic_land_types(
    host: EntryConditionMetricHost,
    destination_controller: str | None,
) -> tuple[str, ...]:
    """Return effective basic land subtypes already under one controller."""

    if destination_controller is None:
        return ()
    basic_types: set[str] = set()
    for permanent_id in host.state.players[
        destination_controller
    ].zones["battlefield"]:
        permanent = host.state.cards[permanent_id]
        if permanent.controller != destination_controller or permanent.phased_out:
            continue
        effective = host._effective_card_data(permanent)
        _, subtypes, _ = host._type_parts(
            str(effective.get("type_line") or "")
        )
        basic_types.update(subtypes.intersection(BASIC_LAND_TYPES))
    return tuple(sorted(basic_types))


def entry_condition_metrics(
    host: EntryConditionMetricHost,
    destination_controller: str | None,
) -> dict[str, int]:
    """Snapshot closed predicates over already-present phased-in permanents."""

    metrics = {metric.value: 0 for metric in FixedEntryMetric}
    active = tuple(host.active_seats)
    if active:
        metrics[FixedEntryMetric.MINIMUM_PLAYER_LIFE.value] = min(
            int(host.state.players[seat].life) for seat in active
        )
    if destination_controller is None:
        return metrics
    basic_metric = {
        "plains": FixedEntryMetric.CONTROLLER_PLAINS,
        "island": FixedEntryMetric.CONTROLLER_ISLANDS,
        "swamp": FixedEntryMetric.CONTROLLER_SWAMPS,
        "mountain": FixedEntryMetric.CONTROLLER_MOUNTAINS,
        "forest": FixedEntryMetric.CONTROLLER_FORESTS,
    }
    for permanent in host.state.cards.values():
        if permanent.zone != "battlefield" or permanent.phased_out:
            continue
        controller = permanent.controller
        if controller not in active:
            continue
        effective = host._effective_card_data(permanent)
        types, subtypes, supertypes = host._type_parts(
            str(effective.get("type_line") or "")
        )
        if "land" in types:
            if controller == destination_controller:
                metrics[FixedEntryMetric.CONTROLLER_LANDS.value] += 1
                if "basic" in supertypes:
                    metrics[
                        FixedEntryMetric.CONTROLLER_BASIC_LANDS.value
                    ] += 1
                for subtype, metric in basic_metric.items():
                    if subtype in subtypes:
                        metrics[metric.value] += 1
            else:
                metrics[FixedEntryMetric.OPPONENT_LANDS.value] += 1
        if controller != destination_controller:
            continue
        if "creature" in types and "legendary" in supertypes:
            metrics[
                FixedEntryMetric.CONTROLLER_LEGENDARY_CREATURES.value
            ] += 1
            colors = {
                str(color).upper()
                for color in effective.get("colors", ())
            }
            if "G" in colors:
                metrics[
                    FixedEntryMetric.CONTROLLER_LEGENDARY_GREEN_CREATURES.value
                ] += 1
        if {"mount", "vehicle"}.intersection(subtypes):
            metrics[
                FixedEntryMetric.CONTROLLER_MOUNTS_OR_VEHICLES.value
            ] += 1
    return metrics


__all__ = [
    "EntryConditionMetricHost",
    "controller_basic_land_types",
    "entry_condition_metrics",
]
