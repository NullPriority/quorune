"""Typed no-permission top exile from a committed land-entry controller."""

from collections.abc import Mapping


LAND_ENTRY_TOP_EXILE_VERSION = 6
LAND_ENTRY_TOP_EXILE_COUNT = 2
LAND_ENTRY_TOP_EXILE_CAPABILITY = "zone.exile.fixed_land_entry_controller_top"
LAND_ENTRY_PLAYER_REFERENCE = "$context.controller"


def is_land_entry_top_exile(effect):
    return (
        isinstance(effect, Mapping) and effect.get("op") == "fixed_impulse_access"
        and type(effect.get("schema_version")) is int
        and effect["schema_version"] == LAND_ENTRY_TOP_EXILE_VERSION
    )


def land_entry_top_exile_effect():
    return {
        "op": "fixed_impulse_access",
        "schema_version": LAND_ENTRY_TOP_EXILE_VERSION,
        "player": LAND_ENTRY_PLAYER_REFERENCE,
        "count": LAND_ENTRY_TOP_EXILE_COUNT,
        "duration": None,
    }


def validate_land_entry_top_exile(effect, *, actor=None):
    expected = land_entry_top_exile_effect()
    if (not is_land_entry_top_exile(effect) or set(effect) != set(expected)
            or type(effect["count"]) is not int or effect["count"] != expected["count"]
            or effect["duration"] is not None):
        raise ValueError("Land-entry top exile requires the fixed two and no play permission")
    supplier = effect["player"]
    if actor is None:
        if supplier != LAND_ENTRY_PLAYER_REFERENCE:
            raise ValueError("Land-entry top exile requires the committed entry controller")
    elif (type(supplier) is not str or not supplier or supplier.startswith("$")
            or supplier == actor):
        raise ValueError("Land-entry top exile requires a distinct resolved supplier")
