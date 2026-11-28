"""Closed opponent library exile followed by a choice from that owner's public exile."""

from collections.abc import Mapping


TARGETED_EXILE_LAND_ENTRY_SCOPE = "targeted_exile_land_entry"
TARGETED_EXILE_LAND_ENTRY_VERSION = 7
TARGETED_EXILE_LAND_ENTRY_COUNT = 4
EXILE_LAND_ENTRY_PAYMENT_FIELD = "land_entry_payment"
TARGETED_EXILE_LAND_ENTRY_CAPABILITY = "zone.exile.target_opponent_top_land_entry"
TARGETED_EXILE_LAND_ENTRY_MECHANIC = "targeted-exile-land-entry"


def targeted_exile_land_entry_instruction():
    return {
        "op": "exile_fixed_library_tops_and_cast",
        "schema_version": TARGETED_EXILE_LAND_ENTRY_VERSION,
        "scope": TARGETED_EXILE_LAND_ENTRY_SCOPE,
        "player": "$controller",
        "supplier": "$target.0",
        "count": TARGETED_EXILE_LAND_ENTRY_COUNT,
    }


def uses_targeted_exile_land_entry(value):
    return isinstance(value, Mapping) and value.get("scope") == TARGETED_EXILE_LAND_ENTRY_SCOPE


def validate_targeted_exile_land_entry(effect, *, resolved=False):
    expected = targeted_exile_land_entry_instruction()
    if not isinstance(effect, Mapping) or set(effect) != set(expected):
        raise ValueError("Opponent exile and land entry requires a closed instruction")
    if type(effect["schema_version"]) is not int or type(effect["count"]) is not int:
        raise ValueError("Opponent exile and land entry requires the versioned fixed count")
    if resolved:
        actor, supplier = effect["player"], effect["supplier"]
        if actor == supplier or any(type(seat) is not str or not seat or seat.startswith("$")
                                    for seat in (actor, supplier)):
            raise ValueError("Land entry requires a distinct resolving controller and targeted owner")
        expected.update(player=actor, supplier=supplier)
    if dict(effect) != expected:
        raise ValueError("Opponent exile and land entry lost its fixed supplier or count")
    return TARGETED_EXILE_LAND_ENTRY_SCOPE
