from __future__ import annotations

"""Closed numeric characteristic predicates for public permanent targets."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class TargetNumericCharacteristic(str, Enum):
    POWER = "power"
    TOUGHNESS = "toughness"
    POWER_OR_TOUGHNESS = "power_or_toughness"
    TOTAL_POWER_AND_TOUGHNESS = "total_power_and_toughness"


class TargetNumericComparison(str, Enum):
    AT_LEAST = "at_least"
    AT_MOST = "at_most"


@dataclass(frozen=True, slots=True)
class TargetNumericCharacteristicSpec:
    """One fixed public power/toughness comparison."""

    characteristic: TargetNumericCharacteristic
    comparison: TargetNumericComparison
    value: int

    def __post_init__(self) -> None:
        if not isinstance(self.characteristic, TargetNumericCharacteristic):
            raise ValueError("Target numeric characteristic is unsupported")
        if not isinstance(self.comparison, TargetNumericComparison):
            raise ValueError("Target numeric comparison is unsupported")
        if type(self.value) is not int or self.value < 0:
            raise ValueError("Target numeric value must be a nonnegative integer")

    def permits(self, *, power: int | None, toughness: int | None) -> bool:
        if self.characteristic is TargetNumericCharacteristic.POWER:
            values = () if power is None else (power,)
        elif self.characteristic is TargetNumericCharacteristic.TOUGHNESS:
            values = () if toughness is None else (toughness,)
        elif self.characteristic is TargetNumericCharacteristic.POWER_OR_TOUGHNESS:
            values = tuple(
                value for value in (power, toughness) if value is not None
            )
        else:
            values = (
                ()
                if power is None or toughness is None
                else (power + toughness,)
            )
        if self.comparison is TargetNumericComparison.AT_LEAST:
            return any(value >= self.value for value in values)
        return any(value <= self.value for value in values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "characteristic": self.characteristic.value,
            "comparison": self.comparison.value,
            "value": self.value,
        }

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "TargetNumericCharacteristicSpec":
        if not isinstance(value, Mapping) or set(value) != {
            "characteristic",
            "comparison",
            "value",
        }:
            raise ValueError(
                "Target numeric predicates require exact typed fields"
            )
        try:
            return cls(
                characteristic=TargetNumericCharacteristic(
                    value["characteristic"]
                ),
                comparison=TargetNumericComparison(value["comparison"]),
                value=value["value"],
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(str(exc)) from exc


__all__ = [
    "TargetNumericCharacteristic",
    "TargetNumericCharacteristicSpec",
    "TargetNumericComparison",
]
