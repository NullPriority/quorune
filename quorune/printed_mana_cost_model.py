from __future__ import annotations

"""Versioned authority for the ordinary printed casting price."""

from collections.abc import Mapping


PRINTED_MANA_COST_VERSION_FIELD = "printed_mana_cost_version"
CURRENT_PRINTED_MANA_COST_VERSION = 1


def _current_version(value) -> int:
    if type(value) is not int or value != CURRENT_PRINTED_MANA_COST_VERSION:
        raise ValueError("Unsupported printed mana cost authority version")
    return value


def read_printed_mana_cost_version(data: Mapping) -> int | None:
    if PRINTED_MANA_COST_VERSION_FIELD not in data:
        return None
    return _current_version(data[PRINTED_MANA_COST_VERSION_FIELD])


def printed_mana_cost_format_fields(version: int | None) -> dict[str, int]:
    return {} if version is None else {PRINTED_MANA_COST_VERSION_FIELD: _current_version(version)}
