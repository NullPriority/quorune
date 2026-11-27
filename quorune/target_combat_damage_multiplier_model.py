"""A resolving creature grant that multiplies combat damage to its controller's opponents."""

from dataclasses import dataclass
from typing import Any, Mapping

from .continuous_effect_model import ContinuousEffectDuration, ContinuousEffectError, ContinuousObjectIdentity


MULTIPLIER_OPERATION = "grant_target_combat_damage_multiplier"
MULTIPLIER_KIND = "resolved_target_combat_damage_multiplier"
MULTIPLIER_CAPABILITY = "damage.replacement.target_combat_opponents_triple_until_next_turn"
MULTIPLIER_MECHANIC = "target-combat-damage-opponents-triple-until-next-turn"


def multiplier_instruction():
    return {"op": MULTIPLIER_OPERATION, "schema_version": 1, "card": "$target.0"}


@dataclass(frozen=True, slots=True)
class TargetCombatDamageMultiplierIntent:
    actor: str
    card: str
    source_id: str

    def __post_init__(self):
        if any(type(value) is not str or not value for value in (self.actor, self.card, self.source_id)):
            raise ValueError("A combat damage multiplier requires the controller, target and stack source")


@dataclass(frozen=True, slots=True)
class ResolvedTargetCombatDamageMultiplier:
    effect_id: str
    source_id: str
    timestamp: int
    expires_on_next_turn_of: str
    target: ContinuousObjectIdentity
    duration: ContinuousEffectDuration = ContinuousEffectDuration.UNTIL_NEXT_TURN

    def __post_init__(self):
        if (
            any(type(value) is not str or not value for value in (
                self.effect_id, self.source_id, self.expires_on_next_turn_of
            ))
            or type(self.timestamp) is not int or self.timestamp < 0
            or not isinstance(self.target, ContinuousObjectIdentity)
            or self.duration is not ContinuousEffectDuration.UNTIL_NEXT_TURN
        ):
            raise ContinuousEffectError("Malformed next-turn combat damage multiplier")

    def to_dict(self):
        return {
            "effect_kind": MULTIPLIER_KIND, "schema_version": 1, "effect_id": self.effect_id,
            "source_id": self.source_id, "timestamp": self.timestamp,
            "expires_on_next_turn_of": self.expires_on_next_turn_of,
            "target": self.target.to_dict(), "duration": self.duration.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]):
        fields = {"effect_kind", "schema_version", "effect_id", "source_id", "timestamp",
                  "expires_on_next_turn_of", "target", "duration"}
        if (
            not isinstance(value, Mapping) or set(value) != fields
            or value["effect_kind"] != MULTIPLIER_KIND
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
        ):
            raise ContinuousEffectError("Invalid combat damage multiplier record")
        return cls(
            value["effect_id"], value["source_id"], value["timestamp"], value["expires_on_next_turn_of"],
            ContinuousObjectIdentity.from_dict(value["target"]), ContinuousEffectDuration(value["duration"]),
        )

    @property
    def fingerprint(self):
        from .replacement.immutable import immutable_fingerprint

        return immutable_fingerprint(self.to_dict())
