from __future__ import annotations

"""Immutable CR 119.7 permission values, separate from replacements."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .continuous_effect_model import ContinuousEffectDuration, ContinuousEffectError
from .replacement.immutable import immutable_fingerprint


LIFE_GAIN_PROHIBITION_CAPABILITY = 'life.gain.prohibition.static'
LIFE_GAIN_PROHIBITION_HANDLER = 'life.gain.prohibition.v1'
LIFE_GAIN_PROHIBITION_EVENT = 'life.gain.permission'
TEMPORARY_LIFE_GAIN_PROHIBITION_FIELD = 'temporary_life_gain_prohibition'
TEMPORARY_LIFE_GAIN_PROHIBITION_KIND = 'temporary_life_gain_prohibition'


@dataclass(frozen=True, slots=True)
class LifeGainPermissionSnapshot:
    denied_players: tuple[str, ...] = ()

    def __post_init__(self):
        if (not isinstance(self.denied_players, tuple)
                or any(type(p) is not str or not p for p in self.denied_players)
                or self.denied_players != tuple(sorted(set(self.denied_players)))):
            raise ValueError('Life-gain permissions require unique canonical player IDs')

    def allows_gain(self, player: str) -> bool:
        if type(player) is not str or not player:
            raise ValueError('Life-gain permissions require a player ID')
        return player not in self.denied_players


ALLOW_ALL_LIFE_GAIN = LifeGainPermissionSnapshot()


@dataclass(frozen=True, slots=True)
class FixedLifeGainProhibitionSpec:
    affected_player_relation: str

    def __post_init__(self):
        if type(self.affected_player_relation) is not str or self.affected_player_relation not in {'any', 'opponent'}:
            raise ValueError('Unsupported static life-gain prohibition relation')

    def to_dict(self):
        return {'schema_version': 1, 'affected_player_relation': self.affected_player_relation}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'schema_version', 'affected_player_relation'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1):
            raise ValueError('Malformed static life-gain prohibition')
        return cls(value['affected_player_relation'])

    def permissions(self, controller: str, active_players: Sequence[str]) -> LifeGainPermissionSnapshot:
        if (isinstance(active_players, (str, bytes)) or not active_players
                or any(type(p) is not str or not p for p in active_players)
                or len(set(active_players)) != len(active_players) or controller not in active_players):
            raise ValueError('Life-gain prohibitions require a current active controller')
        return LifeGainPermissionSnapshot(tuple(sorted(p for p in active_players
            if self.affected_player_relation == 'any' or p != controller)))

@dataclass(frozen=True, slots=True)
class ResolvedLifeGainProhibition:
    effect_id: str
    source_id: str
    timestamp: int
    controller: str
    denied_players: tuple[str, ...]
    duration: ContinuousEffectDuration = ContinuousEffectDuration.UNTIL_END_OF_TURN
    affected_player_relation: str = "opponent"

    def __post_init__(self):
        if (
            any(type(value) is not str or not value for value in (self.effect_id, self.source_id, self.controller))
            or type(self.timestamp) is not int or self.timestamp < 0
            or self.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN
        ):
            raise ContinuousEffectError("Resolved life permission requires its source, controller and cleanup duration")
        LifeGainPermissionSnapshot(self.denied_players)
        spec = FixedLifeGainProhibitionSpec(self.affected_player_relation)
        if (self.controller in self.denied_players) != (spec.affected_player_relation == "any"):
            raise ContinuousEffectError("Resolved life permission does not match its relation")

    def to_dict(self):
        value = {
            "effect_kind": TEMPORARY_LIFE_GAIN_PROHIBITION_KIND, "schema_version": 1,
            "effect_id": self.effect_id, "source_id": self.source_id, "timestamp": self.timestamp,
            "controller": self.controller, "denied_players": list(self.denied_players),
            "duration": self.duration.value,
        }
        if self.affected_player_relation == "any":
            value.update(schema_version=2, affected_player_relation="any")
        return value

    @classmethod
    def from_dict(cls, value):
        fields = {"effect_kind", "schema_version", "effect_id", "source_id", "timestamp",
                  "controller", "denied_players", "duration"}
        if (
            not isinstance(value, Mapping)
            or value.get("effect_kind") != TEMPORARY_LIFE_GAIN_PROHIBITION_KIND
            or type(value.get("schema_version")) is not int
            or not isinstance(value.get("denied_players"), list)
        ):
            raise ContinuousEffectError("Malformed temporary life permission")
        if value["schema_version"] == 1 and set(value) == fields:
            relation = "opponent"
        elif (value["schema_version"] == 2 and set(value) == fields | {"affected_player_relation"}
              and value["affected_player_relation"] == "any"):
            relation = "any"
        else:
            raise ContinuousEffectError("Malformed temporary life permission relation or version")
        return cls(
            value["effect_id"], value["source_id"], value["timestamp"], value["controller"],
            tuple(value["denied_players"]), ContinuousEffectDuration(value["duration"]), relation,
        )

    @property
    def fingerprint(self):
        return immutable_fingerprint(self.to_dict())


def temporary_life_gain_effect(affected_player_relation):
    return {
        "op": "life", "source": "$stack",
        TEMPORARY_LIFE_GAIN_PROHIBITION_FIELD: FixedLifeGainProhibitionSpec(affected_player_relation).to_dict(),
    }


def temporary_opponent_life_gain_effect():
    return temporary_life_gain_effect("opponent")


def validate_temporary_life_gain_effect(effect, *, resolved=False):
    expected = {"op", "source", TEMPORARY_LIFE_GAIN_PROHIBITION_FIELD}
    if resolved:
        expected.update(field for field in ("reason", "_runtime_source") if field in effect)
    source = effect.get("source")
    if (set(effect) != expected or effect.get("op") != "life"
            or type(source) is not str or not source or not resolved and source != "$stack"):
        raise ValueError("Temporary life permission requires its closed resolving source")
    return FixedLifeGainProhibitionSpec.from_dict(effect[TEMPORARY_LIFE_GAIN_PROHIBITION_FIELD])


def decode_life_gain_prohibition(descriptor):
    if (not isinstance(descriptor, Mapping)
            or set(descriptor) != {'handler_id','schema_version','event','condition'}
            or descriptor['handler_id'] != LIFE_GAIN_PROHIBITION_HANDLER
            or type(descriptor['schema_version']) is not int or descriptor['schema_version'] != 1
            or descriptor['event'] != LIFE_GAIN_PROHIBITION_EVENT):
        raise ValueError('Malformed life-gain prohibition descriptor')
    return FixedLifeGainProhibitionSpec.from_dict(descriptor['condition'])


def life_gain_prohibition_payload_dependencies(value, *, coverage=()):
    if not isinstance(value, Mapping) or value.get('handler_id') != LIFE_GAIN_PROHIBITION_HANDLER:
        return ()
    decode_life_gain_prohibition(value)
    from .ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    if CURRENT_ABILITY_FRAGMENT_COVERAGE not in coverage:
        raise ValueError('Life-gain prohibition requires current source-ability scope')
    return (LIFE_GAIN_PROHIBITION_CAPABILITY,)
