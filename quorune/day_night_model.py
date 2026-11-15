from __future__ import annotations

"""Closed schema vocabulary for CR 702.145 and CR 731."""

from dataclasses import dataclass
from enum import Enum
from collections.abc import Mapping


DAY_NIGHT_CAPABILITY_ID = "card_form.day_night"
DAY_NIGHT_MECHANIC_ID = "daybound-and-nightbound"
DAY_NIGHT_DESIGNATION_OPERATION = "set_day_night"
DAY_NIGHT_DESIGNATION_CAPABILITY = "game.day_night.fixed_designation"
DAY_NIGHT_DESIGNATION_MECHANIC = "fixed-day-night-designation"
DAYBOUND_TEMPLATE_ID = "daybound-static-v1"
NIGHTBOUND_TEMPLATE_ID = "nightbound-static-v1"


class DayNightBoundMode(str, Enum):
    DAYBOUND = "daybound"
    NIGHTBOUND = "nightbound"

    @property
    def template_id(self) -> str:
        return (
            DAYBOUND_TEMPLATE_ID
            if self is DayNightBoundMode.DAYBOUND
            else NIGHTBOUND_TEMPLATE_ID
        )


class DayNightError(ValueError):
    """A day/night descriptor, designation, or transition is malformed."""


def validate_day_night_designation(effect):
    if (not isinstance(effect, Mapping) or set(effect)!={'op','designation'}
            or effect['op']!=DAY_NIGHT_DESIGNATION_OPERATION
            or type(effect['designation']) is not str or effect['designation'] not in {'day','night'}):
        raise DayNightError('Day/night instruction requires a closed designation enum')


@dataclass(frozen=True, slots=True)
class DayNightBoundSpec:
    mode: DayNightBoundMode
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1 or type(self.schema_version) is not int:
            raise DayNightError("Unsupported day/night schema version")
        if not isinstance(self.mode, DayNightBoundMode):
            raise DayNightError("Day/night bound mode is invalid")

    @property
    def template_id(self) -> str:
        return self.mode.template_id


__all__ = [
    "DAYBOUND_TEMPLATE_ID",
    "DAY_NIGHT_CAPABILITY_ID",
    "DAY_NIGHT_MECHANIC_ID",
    "DayNightBoundMode",
    "DayNightBoundSpec",
    "DayNightError",
    "NIGHTBOUND_TEMPLATE_ID",
]
