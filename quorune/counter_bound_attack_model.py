"""A resolving effect's attack restriction on counter-bearing incarnations."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .continuous_effect_model import ContinuousEffectDuration, ContinuousObjectIdentity
from .counter_names import normalized_counter_name


COUNTER_BOUND_ATTACK_OPERATION = "restrict_marked_creature_attacks"
COUNTER_BOUND_ATTACK_KIND = "resolution_counter_bound_attack_restriction"
COUNTER_BOUND_ATTACK_CAPABILITY = "combat.attack.restriction.counter_bound_creature"


def _identity(value):
    return type(value) is str and bool(value) and value == value.strip() and not value.startswith("$")


@dataclass(frozen=True, slots=True)
class ResolvedCounterBoundAttackRestriction:
    effect_id: str
    source_id: str
    timestamp: int
    subject: ContinuousObjectIdentity
    protected_player: str
    counter_name: str

    def __post_init__(self):
        if (not all(_identity(value) for value in (self.effect_id, self.source_id, self.protected_player))
                or type(self.timestamp) is not int or self.timestamp < 0
                or not isinstance(self.subject, ContinuousObjectIdentity)
                or not _identity(self.counter_name)
                or normalized_counter_name(self.counter_name) != self.counter_name):
            raise ValueError("A counter-bound attack restriction requires one typed object, counter and protected player")

    @property
    def duration(self):
        # The named-counter and object departure boundaries remove this journal entry.
        return ContinuousEffectDuration.UNTIL_END_OF_GAME

    def validate_players(self, players, *, live=False):
        if self.protected_player not in players or (live and not players[self.protected_player].in_game):
            raise ValueError("An attack restriction requires its known protected player")

    def applies(self, card):
        return (self.subject.object_id == card.object_id
                and self.subject.logical_object_id == card.logical_object_id
                and card.zone == "battlefield" and not card.phased_out
                and card.counters.get(self.counter_name, 0) > 0)

    def to_dict(self):
        return {
            "schema_version": 1, "effect_kind": COUNTER_BOUND_ATTACK_KIND,
            "effect_id": self.effect_id, "source_id": self.source_id, "timestamp": self.timestamp,
            "subject": self.subject.to_dict(), "protected_player": self.protected_player,
            "counter_name": self.counter_name,
        }

    @classmethod
    def from_dict(cls, value):
        fields = {"schema_version", "effect_kind", "effect_id", "source_id", "timestamp",
                  "subject", "protected_player", "counter_name"}
        if (not isinstance(value, Mapping) or set(value) != fields
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["effect_kind"] != COUNTER_BOUND_ATTACK_KIND):
            raise ValueError("A counter-bound attack restriction requires its versioned journal record")
        return cls(value["effect_id"], value["source_id"], value["timestamp"],
                   ContinuousObjectIdentity.from_dict(value["subject"]),
                   value["protected_player"], value["counter_name"])


@dataclass(frozen=True, slots=True)
class CounterBoundAttackRestrictionIntent:
    actor: str
    source_id: str
    counter_name: str
    subjects: tuple[tuple[str, str], ...]

    @classmethod
    def from_effect(cls, effect: Mapping[str, Any], *, actor: str, source_id: str):
        fields = {"op", "schema_version", "protected_player", "counter", "subjects"}
        if (not isinstance(effect, Mapping) or set(effect) != fields
                or effect["op"] != COUNTER_BOUND_ATTACK_OPERATION
                or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
                or effect["protected_player"] != actor or not _identity(actor) or not _identity(source_id)
                or not _identity(effect["counter"])
                or normalized_counter_name(effect["counter"]) != effect["counter"]
                or not isinstance(effect["subjects"], (list, tuple)) or not effect["subjects"]):
            raise ValueError("A counter-marked restriction requires its sealed set and protected controller")
        result = []
        for row in effect["subjects"]:
            if (not isinstance(row, Mapping) or set(row) != {"ref", "logical_object_id"}
                    or not _identity(row["ref"]) or not _identity(row["logical_object_id"])):
                raise ValueError("A counter-marked restriction requires its sealed object incarnations")
            result.append((row["ref"], row["logical_object_id"]))
        if len({ref for ref, _ in result}) != len(result) or len({identity for _, identity in result}) != len(result):
            raise ValueError("Counter-marked recipients must be distinct")
        return cls(actor, source_id, effect["counter"], tuple(result))
