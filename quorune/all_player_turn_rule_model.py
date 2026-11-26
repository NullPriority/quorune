"""Persistent, independently applied turn rules for all players."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from .continuous_effect_model import ContinuousEffectDuration, ContinuousEffectError
from .replacement.immutable import immutable_fingerprint


ALL_PLAYER_TURN_RULE_KIND = "all_player_turn_rule"


class AllPlayerTurnRule(StrEnum):
    HEXPROOF = "hexproof"
    NO_LIFE_LOSS = "no_life_loss"
    NO_GAME_OUTCOME = "no_game_outcome"


@dataclass(frozen=True, slots=True)
class ResolvedAllPlayerTurnRule:
    effect_id: str
    source_id: str
    timestamp: int
    rule: AllPlayerTurnRule
    duration: ContinuousEffectDuration = ContinuousEffectDuration.UNTIL_END_OF_TURN

    def __post_init__(self) -> None:
        if (
            any(type(value) is not str or not value for value in (self.effect_id, self.source_id))
            or type(self.timestamp) is not int or self.timestamp < 0
            or not isinstance(self.rule, AllPlayerTurnRule)
            or self.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN
        ):
            raise ContinuousEffectError("An all-player rule requires a source, rule and cleanup duration")

    def to_dict(self) -> dict:
        return {
            "effect_kind": ALL_PLAYER_TURN_RULE_KIND, "schema_version": 1,
            "effect_id": self.effect_id, "source_id": self.source_id, "timestamp": self.timestamp,
            "rule": self.rule.value, "duration": self.duration.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping) -> "ResolvedAllPlayerTurnRule":
        fields = {"effect_kind", "schema_version", "effect_id", "source_id", "timestamp", "rule", "duration"}
        if (
            not isinstance(value, Mapping) or set(value) != fields
            or value["effect_kind"] != ALL_PLAYER_TURN_RULE_KIND
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
        ):
            raise ContinuousEffectError("Malformed all-player turn rule")
        return cls(value["effect_id"], value["source_id"], value["timestamp"],
                   AllPlayerTurnRule(value["rule"]), ContinuousEffectDuration(value["duration"]))

    @property
    def fingerprint(self) -> str:
        return immutable_fingerprint(self.to_dict())


def has_all_player_turn_rule(journal, kind: AllPlayerTurnRule) -> bool:
    return any(
        isinstance(effect, ResolvedAllPlayerTurnRule) and effect.rule is kind
        for effect in journal or ()
    )
