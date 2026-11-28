"""A sacrifice prohibition locked to one battlefield incarnation through cleanup."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .continuous_effect_model import ContinuousEffectDuration, ContinuousObjectIdentity
from .replacement.immutable import immutable_fingerprint


SACRIFICE_PROHIBITION_KIND = "resolution_sacrifice_prohibition"


@dataclass(frozen=True, slots=True)
class ResolvedSacrificeProhibition:
    effect_id: str
    timestamp: int
    subject: ContinuousObjectIdentity

    def __post_init__(self) -> None:
        if (type(self.effect_id) is not str or not self.effect_id
                or type(self.timestamp) is not int or self.timestamp < 0
                or not isinstance(self.subject, ContinuousObjectIdentity)):
            raise ValueError("Sacrifice prohibition requires an exact permanent incarnation")

    @property
    def duration(self):
        return ContinuousEffectDuration.UNTIL_END_OF_TURN

    @property
    def source_id(self):
        return self.subject.object_id

    @property
    def fingerprint(self):
        return immutable_fingerprint(self.to_dict())

    def to_dict(self):
        return {
            "schema_version": 1, "effect_kind": SACRIFICE_PROHIBITION_KIND,
            "effect_id": self.effect_id, "timestamp": self.timestamp, "subject": self.subject.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping):
        if (not isinstance(value, Mapping)
                or set(value) != {"schema_version", "effect_kind", "effect_id", "timestamp", "subject"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["effect_kind"] != SACRIFICE_PROHIBITION_KIND):
            raise ValueError("Sacrifice prohibition journal fields are closed")
        return cls(value["effect_id"], value["timestamp"], ContinuousObjectIdentity.from_dict(value["subject"]))


def sacrifice_is_prohibited(state, subject) -> bool:
    return any(
        isinstance(effect, ResolvedSacrificeProhibition)
        and effect.subject.object_id == subject.object_id
        and effect.subject.logical_object_id == subject.logical_object_id
        for effect in getattr(state, "continuous_effects", None) or ()
    )
