"""One original attack occurrence supplies the face-up exile library."""

from collections.abc import Mapping


DEFENDING_PLAYER_EXILE_VERSION = 4
DEFENDING_PLAYER_EXILE_CAPABILITY = "zone.exile.defending_player_fixed_top"
DEFENDING_PLAYER_EXILE_COUNT = 20
_FIELDS = {"op", "schema_version", "player", "count", "duration"}


def is_defending_player_exile(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == "fixed_impulse_access"
            and type(effect.get("schema_version")) is int and effect["schema_version"] == DEFENDING_PLAYER_EXILE_VERSION)


def defending_player_exile_effect():
    return {
        "op": "fixed_impulse_access", "schema_version": DEFENDING_PLAYER_EXILE_VERSION,
        "player": "$context.defending_player", "count": DEFENDING_PLAYER_EXILE_COUNT, "duration": None,
    }


def validate_defending_player_exile(effect, *, actor=None):
    if (not is_defending_player_exile(effect) or set(effect) != _FIELDS
            or type(effect["count"]) is not int or effect["count"] != DEFENDING_PLAYER_EXILE_COUNT
            or effect["duration"] is not None):
        raise ValueError("Defending-player exile requires the fixed twenty and face-up exile alone")
    player = effect["player"]
    if actor is None:
        if player != "$context.defending_player":
            raise ValueError("The supplying player must come from the original attack occurrence")
    elif (type(actor) is not str or not actor or actor.startswith("$")
            or type(player) is not str or not player or player.startswith("$") or player == actor):
        raise ValueError("Defending-player exile requires two resolved distinct player roles")
