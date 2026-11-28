"""Typed journal record for controller outcome protection through cleanup."""

from collections.abc import Mapping
from dataclasses import dataclass

from .continuous_effect_model import ContinuousEffectDuration, ContinuousEffectError
from .replacement.immutable import immutable_fingerprint


TURN_OUTCOME_FIELD = "turn_match_outcome_protection"


@dataclass(frozen=True, slots=True)
class ResolvedTurnMatchOutcomeProtection:
    effect_id: str
    source_id: str
    timestamp: int
    controller: str
    duration: ContinuousEffectDuration = ContinuousEffectDuration.UNTIL_END_OF_TURN

    def __post_init__(self) -> None:
        if (
            any(type(value) is not str or not value for value in (self.effect_id, self.source_id, self.controller))
            or type(self.timestamp) is not int or self.timestamp < 0
            or self.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN
        ):
            raise ContinuousEffectError("Turn outcome protection requires its resolving controller and cleanup duration")

    def to_dict(self) -> dict:
        return {
            "effect_kind": TURN_OUTCOME_FIELD, "schema_version": 1,
            "effect_id": self.effect_id, "source_id": self.source_id, "timestamp": self.timestamp,
            "controller": self.controller, "duration": self.duration.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping) -> "ResolvedTurnMatchOutcomeProtection":
        fields = {"effect_kind", "schema_version", "effect_id", "source_id", "timestamp", "controller", "duration"}
        if (
            not isinstance(value, Mapping) or set(value) != fields
            or value["effect_kind"] != TURN_OUTCOME_FIELD
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
        ):
            raise ContinuousEffectError("Malformed turn outcome protection")
        return cls(value["effect_id"], value["source_id"], value["timestamp"], value["controller"],
                   ContinuousEffectDuration(value["duration"]))

    @property
    def fingerprint(self) -> str:
        return immutable_fingerprint(self.to_dict())
