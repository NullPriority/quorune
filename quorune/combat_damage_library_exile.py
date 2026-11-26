"""An original combat-damage recipient supplies two public exile cards."""

from collections.abc import Mapping


COMBAT_DAMAGE_EXILE_VERSION = 7
COMBAT_DAMAGE_EXILE_COUNT = 2
COMBAT_DAMAGE_CAST_EXILE_VERSION = 8
COMBAT_DAMAGE_CAST_EXILE_CAPABILITY = "zone.impulse_access.combat_damage_recipient_cast"
COMBAT_DAMAGE_EXILE_CAPABILITY = "zone.impulse_access.combat_damage_recipient_free"


def combat_damage_exile_effect():
    return {
        "op": "fixed_impulse_access",
        "schema_version": COMBAT_DAMAGE_EXILE_VERSION,
        "player": "$context.target",
        "count": COMBAT_DAMAGE_EXILE_COUNT,
        "duration": "until_end_of_turn",
    }


def is_combat_damage_exile(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == "fixed_impulse_access"
            and type(effect.get("schema_version")) is int
            and effect["schema_version"] == COMBAT_DAMAGE_EXILE_VERSION)


def combat_damage_cast_exile_effect():
    return {**combat_damage_exile_effect(), "schema_version": COMBAT_DAMAGE_CAST_EXILE_VERSION, "count": 1}


def is_combat_damage_cast_exile(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == "fixed_impulse_access"
            and type(effect.get("schema_version")) is int
            and effect["schema_version"] == COMBAT_DAMAGE_CAST_EXILE_VERSION)


def validate_combat_damage_cast_exile(effect, *, actor=None):
    expected = combat_damage_cast_exile_effect()
    if not is_combat_damage_cast_exile(effect) or type(effect.get("count")) is not int:
        raise ValueError("Combat-damage cast access requires its exact top-one instruction")
    player = effect.get("player")
    if actor is not None:
        if (type(actor) is not str or not actor or actor.startswith("$")
                or type(player) is not str or not player or player.startswith("$") or player == actor):
            raise ValueError("Combat-damage cast access requires two resolved player roles")
        expected["player"] = player
    if effect != expected:
        raise ValueError("Combat-damage cast access requires the damaged player and ordinary current-turn casting")


def validate_combat_damage_exile(effect, *, actor=None):
    expected = combat_damage_exile_effect()
    if not is_combat_damage_exile(effect) or type(effect.get("count")) is not int:
        raise ValueError("Combat-damage impulse access requires its closed top-two instruction")
    player = effect.get("player")
    if actor is not None:
        if (type(actor) is not str or not actor or actor.startswith("$")
                or type(player) is not str or not player or player.startswith("$") or player == actor):
            raise ValueError("Combat-damage impulse access requires two resolved player roles")
        expected["player"] = player
    if effect != expected:
        raise ValueError("Combat-damage impulse access requires the damaged player and current-turn free play")
