from __future__ import annotations

"""Versioned authority for the ordinary printed casting price."""

from collections.abc import Mapping


PRINTED_MANA_COST_VERSION_FIELD = "printed_mana_cost_version"
SNOW_MANA_COST_VERSION = 2
STRICT_ANNOUNCED_CAST_X_VERSION = 3
PHYREXIAN_MANA_COST_VERSION = 4
CURRENT_PRINTED_MANA_COST_VERSION = PHYREXIAN_MANA_COST_VERSION
PRINTED_PHYREXIAN_LIFE_FIELD = "printed_phyrexian_life"


def _supported_version(value) -> int:
    if type(value) is not int or value not in (
        1, SNOW_MANA_COST_VERSION, STRICT_ANNOUNCED_CAST_X_VERSION, PHYREXIAN_MANA_COST_VERSION,
    ):
        raise ValueError("Unsupported printed mana cost authority version")
    return value


def read_printed_mana_cost_version(data: Mapping) -> int | None:
    if PRINTED_MANA_COST_VERSION_FIELD not in data:
        return None
    return _supported_version(data[PRINTED_MANA_COST_VERSION_FIELD])


def printed_mana_cost_format_fields(version: int | None) -> dict[str, int]:
    return {} if version is None else {PRINTED_MANA_COST_VERSION_FIELD: _supported_version(version)}


def cast_snow_mana_required(option: Mapping) -> int:
    if "snow" not in option:
        return 0
    amount = option["snow"]
    if type(amount) is not int or amount < 1:
        raise ValueError("A printed snow mana obligation must be a positive integer")
    return amount


def cast_snow_payment_kwargs(option: Mapping) -> dict[str, int]:
    amount = cast_snow_mana_required(option)
    return {"snow_required": amount} if amount else {}


def printed_phyrexian_life_payment(option: Mapping) -> int:
    if PRINTED_PHYREXIAN_LIFE_FIELD not in option:
        return 0
    amount = option[PRINTED_PHYREXIAN_LIFE_FIELD]
    if type(amount) is not int or amount <= 0 or amount % 2:
        raise ValueError("Printed Phyrexian life payments require positive pairs of life")
    return amount
