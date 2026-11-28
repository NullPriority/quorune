"""Persistent turn rules with global or fixed player scope."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from .continuous_effect_model import ContinuousEffectDuration, ContinuousEffectError
from .landwalk_vocabulary import BASIC_LAND_TYPES
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
    player: str | None = None

    def __post_init__(self) -> None:
        if (
            any(type(value) is not str or not value for value in (self.effect_id, self.source_id))
            or type(self.timestamp) is not int or self.timestamp < 0
            or not isinstance(self.rule, AllPlayerTurnRule)
            or self.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN
            or (self.player is not None and (
                type(self.player) is not str or not self.player or self.rule is not AllPlayerTurnRule.HEXPROOF
            ))
        ):
            raise ContinuousEffectError("An all-player rule requires a source, rule and cleanup duration")

    def to_dict(self) -> dict:
        result = {
            "effect_kind": ALL_PLAYER_TURN_RULE_KIND, "schema_version": 1,
            "effect_id": self.effect_id, "source_id": self.source_id, "timestamp": self.timestamp,
            "rule": self.rule.value, "duration": self.duration.value,
        }
        if self.player is not None:
            result.update(schema_version=2, player=self.player)
        return result

    @classmethod
    def from_dict(cls, value: Mapping) -> "ResolvedAllPlayerTurnRule":
        fields = {"effect_kind", "schema_version", "effect_id", "source_id", "timestamp", "rule", "duration"}
        if not isinstance(value, Mapping) or type(value.get("schema_version")) is not int:
            raise ContinuousEffectError("Malformed player turn rule")
        version = value["schema_version"]
        expected_fields = fields | {"player"} if version == 2 else fields
        if (
            set(value) != expected_fields or value["effect_kind"] != ALL_PLAYER_TURN_RULE_KIND
            or version not in {1, 2} or (version == 2 and value["player"] is None)
        ):
            raise ContinuousEffectError("Malformed player turn rule")
        return cls(value["effect_id"], value["source_id"], value["timestamp"],
                   AllPlayerTurnRule(value["rule"]), ContinuousEffectDuration(value["duration"]),
                   player=value.get("player"))

    @property
    def fingerprint(self) -> str:
        return immutable_fingerprint(self.to_dict())


def has_all_player_turn_rule(journal, kind: AllPlayerTurnRule) -> bool:
    return has_player_turn_rule(journal, kind, None)


def has_player_turn_rule(journal, kind: AllPlayerTurnRule, player: str | None) -> bool:
    return any(
        isinstance(effect, ResolvedAllPlayerTurnRule) and effect.rule is kind
        and (effect.player is None or effect.player == player)
        for effect in journal or ()
    )


TURN_LAND_MANA_TRIGGER_KIND = "turn_land_mana_trigger"
TURN_LAND_MANA_TRIGGER_CAPABILITY = "mana.triggered.turn_land_subtype"


@dataclass(frozen=True, slots=True)
class TurnLandManaTriggerSpec:
    land_subtype: str
    symbol: str

    def __post_init__(self):
        if (type(self.land_subtype) is not str or self.land_subtype not in BASIC_LAND_TYPES
                or type(self.symbol) is not str or self.symbol not in {"W", "U", "B", "R", "G"}):
            raise ValueError("Turn mana trigger requires a basic land subtype and one fixed colored mana")

    def to_dict(self):
        return {"land_subtype": self.land_subtype, "symbol": self.symbol}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"land_subtype", "symbol"}:
            raise ValueError("Malformed turn mana trigger specification")
        return cls(value["land_subtype"], value["symbol"])

@dataclass(frozen=True, slots=True)
class ResolvedTurnLandManaTrigger:
    effect_id: str
    source_id: str
    timestamp: int
    spec: TurnLandManaTriggerSpec
    snow: bool = False
    duration: ContinuousEffectDuration = ContinuousEffectDuration.UNTIL_END_OF_TURN

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.effect_id, self.source_id))
                or type(self.timestamp) is not int or self.timestamp < 0
                or not isinstance(self.spec, TurnLandManaTriggerSpec) or type(self.snow) is not bool
                or self.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN):
            raise ValueError("Malformed resolved turn mana trigger")

    def to_dict(self):
        return {
            "effect_kind": TURN_LAND_MANA_TRIGGER_KIND, "schema_version": 1,
            "effect_id": self.effect_id, "source_id": self.source_id, "timestamp": self.timestamp,
            "spec": self.spec.to_dict(), "snow": self.snow, "duration": self.duration.value,
        }

    @classmethod
    def from_dict(cls, value):
        fields = {"effect_kind", "schema_version", "effect_id", "source_id", "timestamp", "spec", "snow", "duration"}
        if (not isinstance(value, Mapping) or set(value) != fields or value["effect_kind"] != TURN_LAND_MANA_TRIGGER_KIND
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Malformed turn mana trigger journal fields")
        return cls(
            value["effect_id"], value["source_id"], value["timestamp"], TurnLandManaTriggerSpec.from_dict(value["spec"]),
            value["snow"], ContinuousEffectDuration(value["duration"]),
        )

    @property
    def fingerprint(self):
        return immutable_fingerprint(self.to_dict())
