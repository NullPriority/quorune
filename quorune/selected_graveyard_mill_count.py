"""Closed live graveyard card counts for the original selected mill players."""

from collections.abc import Mapping
from dataclasses import dataclass


SELECTED_GRAVEYARD_MILL_KIND = "mill_selected_player_graveyard_count"
SELECTED_GRAVEYARD_MILL_CAPABILITY = "zone.mill.selected_player_graveyard_count"


@dataclass(frozen=True, slots=True)
class SelectedGraveyardMillCount:
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported selected graveyard Mill count version")

    def to_dict(self):
        return {"kind": SELECTED_GRAVEYARD_MILL_KIND, "schema_version": self.schema_version}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"kind", "schema_version"}
                or value["kind"] != SELECTED_GRAVEYARD_MILL_KIND):
            raise ValueError("Selected graveyard Mill needs its closed live count")
        return cls(schema_version=value["schema_version"])
