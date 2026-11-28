from __future__ import annotations

"""Immutable life-change permission values, separate from replacements."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .continuous_effect_model import ContinuousEffectDuration, ContinuousEffectError
from .replacement.immutable import immutable_fingerprint


LIFE_GAIN_PROHIBITION_CAPABILITY = 'life.gain.prohibition.static'
LIFE_GAIN_PROHIBITION_HANDLER = 'life.gain.prohibition.v1'
LIFE_GAIN_PROHIBITION_EVENT = 'life.gain.permission'
LIFE_TOTAL_PROHIBITION_CAPABILITY = 'life.change.prohibition.controller'
LIFE_TOTAL_PROHIBITION_HANDLER = 'life.total.prohibition.controller.v1'
TEMPORARY_LIFE_TOTAL_PROHIBITION_CAPABILITY = 'life.change.prohibition.controller_until_end_of_turn'
TEMPORARY_LIFE_GAIN_PROHIBITION_FIELD = 'temporary_life_gain_prohibition'
TEMPORARY_LIFE_GAIN_PROHIBITION_KIND = 'temporary_life_gain_prohibition'
DEALT_DAMAGE_LIFE_GAIN_PROHIBITION_CAPABILITY = 'life.gain.prohibition.damage_recipients_for_game'
DEALT_DAMAGE_LIFE_GAIN_PROHIBITION_FIELD = 'dealt_damage_life_gain_prohibition'
_DEALT_DAMAGE_RELATION = 'damage_recipients'


@dataclass(frozen=True, slots=True)
class LifeGainPermissionSnapshot:
    denied_players: tuple[str, ...] = ()
    denied_loss_players: tuple[str, ...] = ()

    def __post_init__(self):
        for players in (self.denied_players, self.denied_loss_players):
            if (not isinstance(players, tuple)
                    or any(type(player) is not str or not player for player in players)
                    or players != tuple(sorted(set(players)))):
                raise ValueError("Life permissions require unique canonical player IDs")

    def allows_gain(self, player: str) -> bool:
        return _allows_life_change(player, self.denied_players)

    def allows_loss(self, player: str) -> bool:
        return _allows_life_change(player, self.denied_loss_players)


def _allows_life_change(player: str, denied: tuple[str, ...]) -> bool:
    if type(player) is not str or not player:
        raise ValueError("Life permissions require a player ID")
    return player not in denied


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
            or not self._duration_is_valid()
        ):
            raise ContinuousEffectError("Resolved life permission requires its source, controller and a represented duration")
        LifeGainPermissionSnapshot(self.denied_players)
        if self.affected_player_relation == _DEALT_DAMAGE_RELATION:
            if not self.denied_players:
                raise ContinuousEffectError("A game-duration life prohibition requires committed player recipients")
        elif self.denies_loss:
            if self.denied_players != (self.controller,):
                raise ContinuousEffectError("A temporary life-total prohibition requires only its resolving controller")
        else:
            spec = FixedLifeGainProhibitionSpec(self.affected_player_relation)
            if (self.controller in self.denied_players) != (spec.affected_player_relation == "any"):
                raise ContinuousEffectError("Resolved life permission does not match its relation")

    def _duration_is_valid(self) -> bool:
        if self.affected_player_relation == _DEALT_DAMAGE_RELATION:
            return self.duration is ContinuousEffectDuration.UNTIL_END_OF_GAME
        return self.duration is ContinuousEffectDuration.UNTIL_END_OF_TURN or (
            self.duration is ContinuousEffectDuration.UNTIL_NEXT_TURN and self.denies_loss
        )

    @classmethod
    def for_dealt_damage(cls, effect_id, source_id, timestamp, controller, denied_players):
        return cls(effect_id, source_id, timestamp, controller, denied_players,
                   ContinuousEffectDuration.UNTIL_END_OF_GAME, _DEALT_DAMAGE_RELATION)

    @property
    def denies_loss(self) -> bool:
        return self.affected_player_relation == "controller"

    @property
    def expires_on_next_turn_of(self) -> str | None:
        return self.controller if self.duration is ContinuousEffectDuration.UNTIL_NEXT_TURN else None

    def to_dict(self):
        value = {
            "effect_kind": TEMPORARY_LIFE_GAIN_PROHIBITION_KIND, "schema_version": 1,
            "effect_id": self.effect_id, "source_id": self.source_id, "timestamp": self.timestamp,
            "controller": self.controller, "denied_players": list(self.denied_players),
            "duration": self.duration.value,
        }
        if self.affected_player_relation == "any":
            value.update(schema_version=2, affected_player_relation="any")
        elif self.denies_loss:
            version = 4 if self.duration is ContinuousEffectDuration.UNTIL_NEXT_TURN else 3
            value.update(schema_version=version, affected_player_relation="controller")
        elif self.affected_player_relation == _DEALT_DAMAGE_RELATION:
            value.update(schema_version=5, affected_player_relation=_DEALT_DAMAGE_RELATION)
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
        elif (value["schema_version"] in {3, 4} and set(value) == fields | {"affected_player_relation"}
              and value["affected_player_relation"] == "controller"):
            relation = "controller"
        elif (value["schema_version"] == 5 and set(value) == fields | {"affected_player_relation"}
              and value["affected_player_relation"] == _DEALT_DAMAGE_RELATION):
            relation = _DEALT_DAMAGE_RELATION
        else:
            raise ContinuousEffectError("Malformed temporary life permission relation or version")
        duration = {
            4: ContinuousEffectDuration.UNTIL_NEXT_TURN,
            5: ContinuousEffectDuration.UNTIL_END_OF_GAME,
        }.get(value["schema_version"], ContinuousEffectDuration.UNTIL_END_OF_TURN)
        if value["duration"] != duration.value:
            raise ContinuousEffectError("Temporary life permission duration does not match its schema")
        return cls(
            value["effect_id"], value["source_id"], value["timestamp"], value["controller"],
            tuple(value["denied_players"]), duration, relation,
        )

    @property
    def fingerprint(self):
        return immutable_fingerprint(self.to_dict())


def has_dealt_damage_life_gain_prohibition(effect):
    if DEALT_DAMAGE_LIFE_GAIN_PROHIBITION_FIELD not in effect:
        return False
    version = effect[DEALT_DAMAGE_LIFE_GAIN_PROHIBITION_FIELD]
    if type(version) is not int or version != 1:
        raise ValueError("Dealt-damage life prohibition requires its exact instruction version")
    return True


def temporary_life_gain_effect(affected_player_relation):
    return {
        "op": "life", "source": "$stack",
        TEMPORARY_LIFE_GAIN_PROHIBITION_FIELD: FixedLifeGainProhibitionSpec(affected_player_relation).to_dict(),
    }


def temporary_opponent_life_gain_effect():
    return temporary_life_gain_effect("opponent")


def temporary_controller_life_total_prohibition_effect():
    return {
        "op": "life", "source": "$stack",
        TEMPORARY_LIFE_GAIN_PROHIBITION_FIELD: {"schema_version": 2, "affected_player_relation": "controller"},
    }


def validate_temporary_life_gain_effect(effect, *, resolved=False):
    expected = {"op", "source", TEMPORARY_LIFE_GAIN_PROHIBITION_FIELD}
    if resolved:
        expected.update(field for field in ("reason", "_runtime_source") if field in effect)
    source = effect.get("source")
    if (set(effect) != expected or effect.get("op") != "life"
            or type(source) is not str or not source or not resolved and source != "$stack"):
        raise ValueError("Temporary life permission requires its closed resolving source")
    definition = effect[TEMPORARY_LIFE_GAIN_PROHIBITION_FIELD]
    if (isinstance(definition, Mapping) and type(definition.get("schema_version")) is int
            and dict(definition) == {"schema_version": 2, "affected_player_relation": "controller"}):
        return ControllerLifeTotalProhibition()
    if (isinstance(definition, Mapping) and type(definition.get("schema_version")) is int
            and dict(definition) == {"schema_version": 3, "affected_player_relation": "controller",
                                     "duration": ContinuousEffectDuration.UNTIL_NEXT_TURN.value}):
        return ControllerLifeTotalProhibition(ContinuousEffectDuration.UNTIL_NEXT_TURN)
    return FixedLifeGainProhibitionSpec.from_dict(definition)


def decode_life_gain_prohibition(descriptor):
    if (not isinstance(descriptor, Mapping)
            or set(descriptor) != {'handler_id','schema_version','event','condition'}
            or descriptor['handler_id'] != LIFE_GAIN_PROHIBITION_HANDLER
            or type(descriptor['schema_version']) is not int or descriptor['schema_version'] != 1
            or descriptor['event'] != LIFE_GAIN_PROHIBITION_EVENT):
        raise ValueError('Malformed life-gain prohibition descriptor')
    return FixedLifeGainProhibitionSpec.from_dict(descriptor['condition'])


def life_total_prohibition_descriptor() -> dict:
    return {"handler_id": LIFE_TOTAL_PROHIBITION_HANDLER, "schema_version": 1,
            "event": LIFE_GAIN_PROHIBITION_EVENT, "affected_player_relation": "controller"}


def decode_life_total_prohibition(descriptor):
    if (not isinstance(descriptor, Mapping) or type(descriptor.get("schema_version")) is not int
            or dict(descriptor) != life_total_prohibition_descriptor()):
        raise ValueError("Life total prohibition requires its exact current controller")
    return ControllerLifeTotalProhibition()


@dataclass(frozen=True, slots=True)
class ControllerLifeTotalProhibition:
    duration: ContinuousEffectDuration = ContinuousEffectDuration.UNTIL_END_OF_TURN

    @property
    def affected_player_relation(self) -> str:
        return "controller"

    def permissions(self, controller: str, active_players: Sequence[str]) -> LifeGainPermissionSnapshot:
        if type(controller) is not str or not controller or controller not in active_players:
            raise ValueError("Life total prohibition requires an active controller")
        return LifeGainPermissionSnapshot((controller,), (controller,))


def life_gain_prohibition_payload_dependencies(value, *, coverage=()):
    if not isinstance(value, Mapping):
        return ()
    handler = value.get("handler_id")
    if handler == LIFE_TOTAL_PROHIBITION_HANDLER:
        decode_life_total_prohibition(value)
        capability = LIFE_TOTAL_PROHIBITION_CAPABILITY
    elif handler == LIFE_GAIN_PROHIBITION_HANDLER:
        decode_life_gain_prohibition(value)
        capability = LIFE_GAIN_PROHIBITION_CAPABILITY
    else:
        return ()
    from .ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    if CURRENT_ABILITY_FRAGMENT_COVERAGE not in coverage:
        raise ValueError("Life prohibition requires current source-ability scope")
    return (capability,)
