from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .continuous_effect_model import ContinuousEffectDuration, ContinuousObjectIdentity
from .replacement.immutable import immutable_fingerprint
from .source_continuity import SourceContinuitySnapshot


SOURCE_UNTAP_RESTRAINT_KIND = "resolution_source_untap_restraint"


@dataclass(frozen=True, slots=True)
class PendingSourceUntapRestraint:
    source: ContinuousObjectIdentity | None
    history: SourceContinuitySnapshot | None
    resolution_timestamp: int

    def __post_init__(self) -> None:
        if self.source is not None and not isinstance(self.source, ContinuousObjectIdentity):
            raise ValueError("Untap restraint source must be an original incarnation")
        if self.history is not None and not isinstance(self.history, SourceContinuitySnapshot):
            raise ValueError("Untap restraint history must be a retained snapshot")
        if type(self.resolution_timestamp) is not int or self.resolution_timestamp < 0:
            raise ValueError("Untap restraint requires a resolution timestamp")


@dataclass(frozen=True, slots=True)
class ResolvedSourceUntapRestraint:
    effect_id: str
    timestamp: int
    duration_source: ContinuousObjectIdentity
    duration_history: SourceContinuitySnapshot
    locked_objects: tuple[ContinuousObjectIdentity, ...]

    def __post_init__(self) -> None:
        if type(self.effect_id) is not str or not self.effect_id:
            raise ValueError("Untap restraint identity is malformed")
        if type(self.timestamp) is not int or self.timestamp < 0:
            raise ValueError("Untap restraint timestamp is malformed")
        if not isinstance(self.duration_source, ContinuousObjectIdentity) or not isinstance(
            self.duration_history, SourceContinuitySnapshot,
        ):
            raise ValueError("Untap restraint requires a retained original source interval")
        if (
            type(self.locked_objects) is not tuple
            or not self.locked_objects
            or any(not isinstance(subject, ContinuousObjectIdentity) for subject in self.locked_objects)
            or len({subject.object_id for subject in self.locked_objects}) != len(self.locked_objects)
        ):
            raise ValueError("Untap restraint requires distinct locked subject incarnations")

    @property
    def source_id(self) -> str:
        return self.duration_source.object_id

    @property
    def duration(self) -> ContinuousEffectDuration:
        return ContinuousEffectDuration.UNTIL_SOURCE_LEAVES_OR_UNTAPS

    def to_dict(self):
        return {
            "schema_version": 1,
            "effect_kind": SOURCE_UNTAP_RESTRAINT_KIND,
            "effect_id": self.effect_id,
            "timestamp": self.timestamp,
            "duration_source": self.duration_source.to_dict(),
            "duration_history": self.duration_history.to_dict(),
            "locked_objects": [subject.to_dict() for subject in self.locked_objects],
        }

    @classmethod
    def from_dict(cls, value: Mapping):
        fields = {"schema_version", "effect_kind", "effect_id", "timestamp", "duration_source", "duration_history", "locked_objects"}
        if (
            not isinstance(value, Mapping) or set(value) != fields
            or value["effect_kind"] != SOURCE_UNTAP_RESTRAINT_KIND
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["locked_objects"], list)
        ):
            raise ValueError("Untap restraint journal fields are closed")
        return cls(
            value["effect_id"], value["timestamp"],
            ContinuousObjectIdentity.from_dict(value["duration_source"]),
            SourceContinuitySnapshot.from_dict(value["duration_history"]),
            tuple(ContinuousObjectIdentity.from_dict(subject) for subject in value["locked_objects"]),
        )

    @property
    def fingerprint(self):
        return immutable_fingerprint(self.to_dict())
