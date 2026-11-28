from __future__ import annotations

"""Closed fixed metrics for conditional battlefield entry tap state."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


FIXED_ENTRY_CONDITION_HANDLER_ID = (
    "replacement.zone.entry-state-fixed-condition.v1"
)


class FixedEntryMetric(StrEnum):
    CONTROLLER_LANDS = "controller_lands"
    CONTROLLER_BASIC_LANDS = "controller_basic_lands"
    CONTROLLER_PLAINS = "controller_plains"
    CONTROLLER_ISLANDS = "controller_islands"
    CONTROLLER_SWAMPS = "controller_swamps"
    CONTROLLER_MOUNTAINS = "controller_mountains"
    CONTROLLER_FORESTS = "controller_forests"
    CONTROLLER_LEGENDARY_CREATURES = "controller_legendary_creatures"
    CONTROLLER_LEGENDARY_GREEN_CREATURES = (
        "controller_legendary_green_creatures"
    )
    CONTROLLER_MOUNTS_OR_VEHICLES = "controller_mounts_or_vehicles"
    OPPONENT_LANDS = "opponent_lands"
    MINIMUM_PLAYER_LIFE = "minimum_player_life"
    CONTROLLER_ACTIVE_TURN_NUMBER = "controller_active_turn_number"


class FixedEntryConditionError(ValueError):
    """A fixed entry-state condition is malformed."""


@dataclass(frozen=True, slots=True)
class FixedEntryCondition:
    metric: FixedEntryMetric
    minimum: int | None
    maximum: int | None
    tapped_when_met: bool

    def __post_init__(self) -> None:
        if not isinstance(self.metric, FixedEntryMetric):
            raise FixedEntryConditionError(
                "Fixed entry conditions require a typed metric"
            )
        for field_name in ("minimum", "maximum"):
            value = getattr(self, field_name)
            if value is not None and (type(value) is not int or value < 0):
                raise FixedEntryConditionError(
                    f"Fixed entry {field_name} must be nonnegative or null"
                )
        if self.minimum is None and self.maximum is None:
            raise FixedEntryConditionError(
                "Fixed entry conditions require a bound"
            )
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise FixedEntryConditionError(
                "Fixed entry condition bounds are reversed"
            )
        if self.metric is FixedEntryMetric.CONTROLLER_ACTIVE_TURN_NUMBER and (
            self.minimum, self.maximum, self.tapped_when_met
        ) != (1, 3, False):
            raise FixedEntryConditionError(
                "The controller turn condition requires exactly the first three active turns"
            )
        if type(self.tapped_when_met) is not bool:
            raise FixedEntryConditionError(
                "Fixed entry tap polarity must be boolean"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric.value,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "tapped_when_met": self.tapped_when_met,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixedEntryCondition":
        if not isinstance(value, Mapping) or set(value) != {
            "metric",
            "minimum",
            "maximum",
            "tapped_when_met",
        }:
            raise FixedEntryConditionError(
                "Fixed entry condition fields are closed"
            )
        try:
            metric = FixedEntryMetric(value["metric"])
        except (TypeError, ValueError) as exc:
            raise FixedEntryConditionError(
                "Unknown fixed entry condition metric"
            ) from exc
        return cls(
            metric=metric,
            minimum=value["minimum"],
            maximum=value["maximum"],
            tapped_when_met=value["tapped_when_met"],
        )

    def is_met(self, metrics: Mapping[str, Any]) -> bool:
        if not isinstance(metrics, Mapping):
            raise FixedEntryConditionError(
                "Fixed entry metrics must be a mapping"
            )
        value = metrics.get(self.metric.value)
        if type(value) is not int or (
            value < 0 and self.metric is not FixedEntryMetric.MINIMUM_PLAYER_LIFE
        ):
            raise FixedEntryConditionError(
                f"Fixed entry metric {self.metric.value} is unavailable"
            )
        return (
            (self.minimum is None or value >= self.minimum)
            and (self.maximum is None or value <= self.maximum)
        )


__all__ = [
    "FIXED_ENTRY_CONDITION_HANDLER_ID",
    "FixedEntryCondition",
    "FixedEntryConditionError",
    "FixedEntryMetric",
]
