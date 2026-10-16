from __future__ import annotations

"""Closed public values chosen by intrinsic battlefield-entry replacements."""

from enum import StrEnum
from typing import Literal

from .creature_subtypes import CREATURE_SUBTYPES, canonical_creature_subtype



ENTRY_DESIGNATION_HANDLER_ID = "replacement.zone.entry-designation.v1"
ENTRY_DESIGNATION_CAPABILITY = "zone.entry.public_designation"
EXCLUDED_COLOR_ENTRY_HANDLER_ID = "replacement.zone.entry-color-exclusion.v2"
ENTRY_COLOR_COMPOSITION_CAPABILITY = "zone.entry.color_designation_composition"
ENTRY_DESIGNATION_HANDLER_IDS = (ENTRY_DESIGNATION_HANDLER_ID, EXCLUDED_COLOR_ENTRY_HANDLER_ID)
CHOSEN_CHARACTERISTICS_HANDLER_ID = "continuous.characteristics.chosen-designation.v1"
CHOSEN_CHARACTERISTICS_CAPABILITY = "continuous.characteristics.chosen_designation"
CONDITIONAL_CHOSEN_CHARACTERISTICS_HANDLER_ID = "continuous.characteristics.conditional-chosen-designation.v1"
CONDITIONAL_CHOSEN_CHARACTERISTICS_CAPABILITY = "continuous.characteristics.conditional_chosen_designation"


class EntryDesignationKind(StrEnum):
    COLOR = "color"
    CREATURE_TYPE = "creature_type"

    @property
    def annotation(self) -> Literal["chosen_color", "chosen_creature_type"]:
        return "chosen_color" if self is self.COLOR else "chosen_creature_type"

    @property
    def event_field(self) -> str:
        return "entry_" + self.annotation

    @property
    def values(self) -> tuple[str, ...]:
        return (
            tuple("WUBRG")
            if self is self.COLOR
            else tuple(sorted(CREATURE_SUBTYPES))
        )


def validate_designation(kind: EntryDesignationKind, value: object) -> str:
    if type(value) is not str:
        raise ValueError("An entry designation must be a canonical public value")
    if kind is EntryDesignationKind.COLOR:
        if value not in tuple("WUBRG"):
            raise ValueError("A chosen color must be one WUBRG color")
    elif canonical_creature_subtype(value) != value:
        raise ValueError("A chosen creature type must use the pinned vocabulary")
    return value
