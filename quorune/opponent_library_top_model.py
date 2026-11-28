"""One targeted opponent supplies a face-up library top to exile."""

from collections.abc import Mapping
from dataclasses import dataclass

from .fractional_quantity_model import HalfQuantitySpec


OPPONENT_TOP_EXILE_VERSION = 3
OPPONENT_HALF_TOP_EXILE_VERSION = 5
OPPONENT_HALF_TOP_EXILE_CAPABILITY = "zone.exile.target_opponent_top_half"
OPPONENT_TOP_EXILE_CAPABILITY = "zone.exile.fixed_target_opponent_top"
OPPONENT_TOP_EXILE_FIELDS = {"op", "schema_version", "player", "count", "duration"}
_END_OF_TURN = "until_end_of_turn"
_ROUNDED_UP = HalfQuantitySpec("up")


def is_opponent_top_exile(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == "fixed_impulse_access"
            and type(effect.get("schema_version")) is int and effect["schema_version"] == OPPONENT_TOP_EXILE_VERSION)


@dataclass(frozen=True, slots=True)
class OpponentLibraryTopSpec:
    allows_play: bool

    def __post_init__(self):
        if type(self.allows_play) is not bool:
            raise ValueError("Opponent library exile requires a closed play-permission policy")

    def effect(self):
        return {
            "op": "fixed_impulse_access", "schema_version": OPPONENT_TOP_EXILE_VERSION,
            "player": "$target.0", "count": 1, "duration": _END_OF_TURN if self.allows_play else None,
        }

    @classmethod
    def from_effect(cls, value, *, actor=None):
        if (not is_opponent_top_exile(value) or set(value) != OPPONENT_TOP_EXILE_FIELDS
                or type(value["count"]) is not int or value["count"] != 1
                or value["duration"] not in (None, _END_OF_TURN)):
            raise ValueError("Opponent library exile requires one public card and its printed permission")
        player = value["player"]
        if actor is None:
            if player != "$target.0":
                raise ValueError("The exiling player must be the original target")
        elif (type(actor) is not str or not actor or actor.startswith("$")
                or type(player) is not str or not player or player.startswith("$") or player == actor):
            raise ValueError("Opponent library exile requires a distinct resolved target")
        return cls(value["duration"] == _END_OF_TURN)


def is_opponent_half_top_exile(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == "fixed_impulse_access"
            and type(effect.get("schema_version")) is int and effect["schema_version"] == OPPONENT_HALF_TOP_EXILE_VERSION)


def opponent_half_top_exile_effect():
    return {
        "op": "fixed_impulse_access", "schema_version": OPPONENT_HALF_TOP_EXILE_VERSION,
        "player": "$target.0", "count": _ROUNDED_UP.to_dict(), "duration": None,
    }


def validate_opponent_half_top_exile(effect, *, actor=None):
    if (not is_opponent_half_top_exile(effect) or set(effect) != OPPONENT_TOP_EXILE_FIELDS
            or effect["duration"] is not None or HalfQuantitySpec.from_dict(effect["count"]) != _ROUNDED_UP):
        raise ValueError("Opponent top-half exile requires mandatory face-up exile rounded up")
    player = effect["player"]
    if actor is None:
        if player != "$target.0":
            raise ValueError("Opponent top-half exile requires its original target")
    elif (type(actor) is not str or not actor or actor.startswith("$")
            or type(player) is not str or not player or player.startswith("$") or actor == player):
        raise ValueError("Opponent top-half exile requires distinct resolved player roles")
    return _ROUNDED_UP
