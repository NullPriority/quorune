"""Resolution-created restrictions on attacks against protected defenders."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .continuous_effect_model import ContinuousEffectDuration


ATTACK_RESTRICTION_OPERATION = "restrict_player_attacks_during_next_turn"
ATTACK_RESTRICTION_EFFECT_KIND = "resolution_next_turn_player_attack_restriction"
ATTACK_RESTRICTION_CAPABILITY = "combat.attack.restriction.player_during_next_turn"
UNTIL_PROTECTED_TURN_CAPABILITY = "combat.attack.restriction.until_protected_turn"


def _identity(value: Any) -> bool:
    return type(value) is str and bool(value) and value == value.strip() and not value.startswith("$")


@dataclass(frozen=True, slots=True)
class ResolvedNextTurnAttackRestriction:
    effect_id: str
    source_id: str
    timestamp: int
    restricted_player: str
    protected_player: str
    own_turn: int | None

    def __post_init__(self) -> None:
        if (not all(_identity(value) for value in (
                self.effect_id, self.source_id, self.restricted_player, self.protected_player))
                or self.restricted_player == self.protected_player
                or type(self.timestamp) is not int or self.timestamp < 0
                or self.own_turn is not None and (type(self.own_turn) is not int or self.own_turn < 1)):
            raise ValueError("An attack restriction requires distinct players and a positive turn ordinal")

    @property
    def duration(self) -> ContinuousEffectDuration:
        if self.own_turn is None:
            return ContinuousEffectDuration.UNTIL_NEXT_TURN
        return ContinuousEffectDuration.UNTIL_END_OF_TURN

    @property
    def expires_on_next_turn_of(self) -> str | None:
        return self.protected_player if self.own_turn is None else None

    def validate_players(self, players: Mapping[str, Any], *, live: bool = False) -> None:
        for seat in (self.restricted_player, self.protected_player):
            if seat not in players or (live and not players[seat].in_game):
                raise ValueError("An attack restriction requires its known players")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1 if self.own_turn is not None else 2,
            "effect_kind": ATTACK_RESTRICTION_EFFECT_KIND,
            "effect_id": self.effect_id, "source_id": self.source_id, "timestamp": self.timestamp,
            "restricted_player": self.restricted_player, "protected_player": self.protected_player,
            **({"own_turn": self.own_turn} if self.own_turn is not None
               else {"expires_on_next_turn_of": self.protected_player}),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResolvedNextTurnAttackRestriction":
        fields = {"schema_version", "effect_kind", "effect_id", "source_id", "timestamp",
                  "restricted_player", "protected_player"}
        if (not isinstance(value, Mapping) or not fields <= set(value)
                or type(value["schema_version"]) is not int or value["schema_version"] not in (1, 2)
                or value["effect_kind"] != ATTACK_RESTRICTION_EFFECT_KIND):
            raise ValueError("A next-turn attack restriction requires its complete versioned journal record")
        if value["schema_version"] == 1:
            if set(value) != fields | {"own_turn"} or type(value["own_turn"]) is not int:
                raise ValueError("A restricted turn needs its exact positive ordinal")
        elif (set(value) != fields | {"expires_on_next_turn_of"}
              or value["expires_on_next_turn_of"] != value["protected_player"]):
            raise ValueError("The restriction must expire on the protected player's next turn")
        return cls(
            value["effect_id"], value["source_id"], value["timestamp"],
            value["restricted_player"], value["protected_player"], value.get("own_turn"),
        )

    def applies(self, state: Any, attacker: str) -> bool:
        return (self.restricted_player == attacker == state.active_player
                and (self.own_turn is None or state.players[attacker].turns_begun == self.own_turn))

    def expires_at_cleanup(self, state: Any) -> bool:
        restricted = state.players[self.restricted_player]
        if self.own_turn is None:
            return not restricted.in_game
        return (not restricted.in_game or not state.players[self.protected_player].in_game
                or (state.active_player == self.restricted_player and restricted.turns_begun >= self.own_turn))


@dataclass(frozen=True, slots=True)
class NextTurnAttackRestrictionIntent:
    actor: str
    source_id: str
    opponents: tuple[str, ...]

    def __post_init__(self) -> None:
        if (not _identity(self.actor) or not _identity(self.source_id)
                or not isinstance(self.opponents, tuple) or not self.opponents
                or not all(_identity(value) for value in self.opponents)
                or len(set(self.opponents)) != len(self.opponents) or self.actor in self.opponents):
            raise ValueError("A next-turn attack restriction requires its original controller and distinct opponents")

    @classmethod
    def from_effect(cls, value: Mapping[str, Any], *, actor: str, source_id: str) -> "NextTurnAttackRestrictionIntent":
        if (not isinstance(value, Mapping) or set(value) != {"op", "schema_version", "protected_player", "opponents"}
                or value["op"] != ATTACK_RESTRICTION_OPERATION
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["protected_player"] != actor or not isinstance(value["opponents"], (list, tuple))):
            raise ValueError("A next-turn attack restriction requires its sealed result and protected controller")
        return cls(actor, source_id, tuple(value["opponents"]))
