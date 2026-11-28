"""A sealed set of creatures and its selected player's next actual turn."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .continuous_effect_model import ContinuousEffectDuration, ContinuousObjectIdentity


KIND = "resolution_chosen_attack_turn"
CHOOSE = "choose_creatures_for_next_attack_turn"
COMMIT = "schedule_chosen_attack_turn"
DESTROY = "destroy_chosen_nonattackers"
CAPABILITY = "combat.attack.chosen_set_next_turn"
DELAYED_CAPABILITY = "trigger.delayed.chosen_nonattackers"
MECHANIC = "chosen-creatures-next-attack-turn"
DELAYED_FIELD = "chosen_attack_turn_end_step"


@dataclass(frozen=True, slots=True)
class ChosenAttackTurn:
    player: str
    own_turn: int
    selected: tuple[ContinuousObjectIdentity, ...]

    def __post_init__(self):
        if (type(self.player) is not str or not self.player or self.player.startswith("$")
                or type(self.own_turn) is not int or self.own_turn < 1):
            raise ValueError("A chosen attack turn requires one actual player-turn ordinal")
        if (not isinstance(self.selected, tuple)
                or any(not isinstance(identity, ContinuousObjectIdentity) for identity in self.selected)
                or len({identity.object_id for identity in self.selected}) != len(self.selected)):
            raise ValueError("A chosen attack turn requires distinct selected incarnations")

    def to_dict(self):
        return {"player": self.player, "own_turn": self.own_turn,
                "selected": [identity.to_dict() for identity in self.selected]}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"player", "own_turn", "selected"}
                or not isinstance(value["selected"], (list, tuple))):
            raise ValueError("A chosen attack turn has a closed receipt")
        return cls(value["player"], value["own_turn"], tuple(
            ContinuousObjectIdentity.from_dict(item) for item in value["selected"]
        ))

    def is_current(self, state, player):
        return (state.active_player == player == self.player and state.players[player].in_game
                and state.players[player].turns_begun == self.own_turn)

    def has_ended(self, state):
        player = state.players.get(self.player)
        return (player is None or not player.in_game or player.turns_begun > self.own_turn
                or player.turns_begun == self.own_turn and state.active_player != self.player)


@dataclass(frozen=True, slots=True)
class ResolvedChosenAttackTurn:
    effect_id: str
    source_id: str
    timestamp: int
    turn: ChosenAttackTurn

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.effect_id, self.source_id))
                or type(self.timestamp) is not int or self.timestamp < 0
                or not isinstance(self.turn, ChosenAttackTurn)):
            raise ValueError("The chosen-attack journal entry is malformed")

    @property
    def duration(self):
        return ContinuousEffectDuration.UNTIL_END_OF_TURN

    def expires_at_cleanup(self, state):
        return self.turn.has_ended(state) or self.turn.is_current(state, self.turn.player)

    def to_dict(self):
        return {"effect_kind": KIND, "schema_version": 1, "effect_id": self.effect_id,
                "source_id": self.source_id, "timestamp": self.timestamp, "turn": self.turn.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != {"effect_kind", "schema_version", "effect_id", "source_id", "timestamp", "turn"}
                or value["effect_kind"] != KIND or type(value["schema_version"]) is not int
                or value["schema_version"] != 1):
            raise ValueError("The chosen-attack journal entry needs its exact schema")
        return cls(value["effect_id"], value["source_id"], value["timestamp"],
                   ChosenAttackTurn.from_dict(value["turn"]))
