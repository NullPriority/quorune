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


DAY_ENTRY_CAPABILITY = "card_form.initial_day_as_enters"
DAY_ENTRY_HANDLER = "replacement.zone.initial_day.v1"
DAY_ENTRY_FIELD = "entry_initial_day"
DAY_TOGGLE_EVENT = "game.day_night.changed"
DAY_TOGGLE_COUNTER_CAPABILITY = "counter.producer.day_night_toggle"
DAY_TOGGLE_COUNTER_VARIANT = "day_night_toggle_source_counter"


def day_entry_descriptor():
    return {"handler_id":DAY_ENTRY_HANDLER, "schema_version":1, "event":"zone.change"}


def day_night_toggle_condition():
    return {"any": [{"all": [{"field":"from", "op":"eq", "value":before},
                             {"field":"to", "op":"eq", "value":after}]}
                    for before, after in (("day","night"),("night","day"))]}


def day_night_toggle_runtime_dependencies(program):
    if program.event != DAY_TOGGLE_EVENT and DAY_TOGGLE_COUNTER_CAPABILITY not in program.capability_dependencies:
        return ()
    from .ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from .replacement.immutable import thaw_value

    expected = {"op":"place_counters", "card":"$source.zone_object", "counter":"+1/+1", "amount":1, "source":"$source"}
    if ((program.event, program.active_zone) != (DAY_TOGGLE_EVENT, "battlefield")
            or thaw_value(program.event_condition) != day_night_toggle_condition()
            or tuple(program.effects) != (expected,) or type(program.effects[0]["amount"]) is not int
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or program.trigger_limit is not None or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage):
        raise ValueError("Day/night counters require the original untargeted battlefield source toggle.")
    return (DAY_TOGGLE_COUNTER_CAPABILITY, "counter.producer.fixed_effect",
            "trigger.event.normalized_public_action", "trigger.placement.apnap")
