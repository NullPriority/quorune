from __future__ import annotations

"""Closed rules instruction for source-permanent Endure."""

from collections.abc import Mapping
from typing import Any


ENDURE_OPERATION = "endure"
ENDURE_MECHANIC = "fixed-source-endure"
ENDURE_CAPABILITY = "keyword_action.endure.source"
ENDURE_FIELDS = frozenset({"op", "schema_version", "amount"})


def fixed_endure_amount(effect: Mapping[str, Any]) -> int:
    if (
        not isinstance(effect, Mapping)
        or set(effect) != ENDURE_FIELDS
        or effect["op"] != ENDURE_OPERATION
        or type(effect["schema_version"]) is not int
        or effect["schema_version"] != 1
        or type(effect["amount"]) is not int
        or effect["amount"] < 0
    ):
        raise ValueError("Endure requires a closed nonnegative fixed amount")
    return effect["amount"]
