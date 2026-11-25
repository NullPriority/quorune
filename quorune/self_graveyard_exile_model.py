from __future__ import annotations

"""Closed vocabulary for a current source's own graveyard redirection."""

from collections.abc import Mapping
from dataclasses import dataclass

from .card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD


SELF_GRAVEYARD_EXILE_CAPABILITY = 'zone.change.self_graveyard_exile'
SELF_GRAVEYARD_EXILE_HANDLER = 'replacement.zone.self-graveyard-exile.v1'


SELF_GRAVEYARD_SHUFFLE_HANDLER = "replacement.zone.self-graveyard-library-shuffle.v1"
SELF_GRAVEYARD_SHUFFLE_CAPABILITY = "zone.change.self_graveyard_owner_shuffle"
SELF_GRAVEYARD_SHUFFLE_DEPENDENCIES = (
    SELF_GRAVEYARD_SHUFFLE_CAPABILITY, "zone.change.destination_replacement", "library.shuffle.fixed_controller",
)
SELF_GRAVEYARD_SHUFFLE_OPERATION = "reveal_for_owner_library_shuffle"
SELF_GRAVEYARD_SHUFFLE_RESULT = "self_graveyard_owner_shuffle"


@dataclass(frozen=True, slots=True)
class RevealForOwnerLibraryShuffle:
    object_id: str
    logical_object_id: str
    object_ref: str
    owner: str
    is_commander: bool
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.is_commander) is not bool
                or any(type(value) is not str or not value for value in (
                    self.object_id, self.logical_object_id, self.object_ref, self.owner,
                ))):
            raise ValueError("A self-shuffle needs the original public replacement subject and its owner")

    def to_dict(self):
        return {
            "op": SELF_GRAVEYARD_SHUFFLE_OPERATION, "schema_version": self.schema_version,
            "object_id": self.object_id, "logical_object_id": self.logical_object_id,
            "object_ref": self.object_ref, "owner": self.owner, "is_commander": self.is_commander,
        }

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != {"op", "schema_version", "object_id", "logical_object_id",
                                  "object_ref", "owner", "is_commander"}
                or value["op"] != SELF_GRAVEYARD_SHUFFLE_OPERATION):
            raise ValueError("Self-shuffle replacement fields are closed")
        return cls(**{key: item for key, item in value.items() if key != "op"})


def self_graveyard_shuffle_descriptor():
    return {"handler_id": SELF_GRAVEYARD_SHUFFLE_HANDLER, "schema_version": 1,
            "event": "zone.change", REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True}


def self_graveyard_exile_descriptor():
    return {'handler_id': SELF_GRAVEYARD_EXILE_HANDLER, 'schema_version': 1,
        'event': 'zone.change', REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True}
