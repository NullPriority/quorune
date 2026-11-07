from __future__ import annotations

"""The remembered untap and attachment identity of a phased-out permanent."""

from collections.abc import Mapping
from dataclasses import dataclass

from .continuous_effect_model import ContinuousObjectIdentity


PHASING_CAPABILITY = "untap.phasing.keyword"
PHASING_EVENT = "untap.phasing"
PHASING_HANDLER_ID = "untap.phasing.printed.v1"


def phasing_lifecycle_version(value):
    if value is not None and (type(value) is not int or value != 1):
        raise ValueError("Unsupported phasing lifecycle version")
    return value


@dataclass(frozen=True, slots=True)
class PhasingRecord:
    return_seat: str
    attached_object: ContinuousObjectIdentity | None = None
    attached_player: str | None = None
    indirect: bool = False
    departed_ready: bool = False

    def __post_init__(self):
        if type(self.return_seat) is not str or not self.return_seat:
            raise ValueError("Phasing requires the controller at phase-out")
        if self.attached_object is not None and not isinstance(self.attached_object, ContinuousObjectIdentity):
            raise ValueError("Phasing attachment requires a current object identity")
        if self.attached_player is not None and (
            type(self.attached_player) is not str or not self.attached_player or self.attached_object is not None
        ):
            raise ValueError("Phasing attachment player is malformed")
        if type(self.indirect) is not bool or type(self.departed_ready) is not bool:
            raise ValueError("Phasing flags must be boolean")
        if self.indirect and (self.attached_object is None or self.departed_ready):
            raise ValueError("Indirect phasing follows one attached permanent")

    def to_dict(self):
        return {
            "return_seat": self.return_seat,
            "attached_object": self.attached_object.to_dict() if self.attached_object is not None else None,
            "attached_player": self.attached_player,
            "indirect": self.indirect,
            "departed_ready": self.departed_ready,
        }

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {
            "return_seat", "attached_object", "attached_player", "indirect", "departed_ready",
        }:
            raise ValueError("Phasing record fields are closed")
        attached = value["attached_object"]
        return cls(
            return_seat=value["return_seat"],
            attached_object=ContinuousObjectIdentity.from_dict(attached) if attached is not None else None,
            attached_player=value["attached_player"], indirect=value["indirect"], departed_ready=value["departed_ready"],
        )
