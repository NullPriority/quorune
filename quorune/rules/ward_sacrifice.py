"""The public battlefield criteria for a single-permanent Ward sacrifice."""

from __future__ import annotations

from ..object_query import ObjectQueryResult
from ..trigger_participation import WardSacrificeScope


def ward_sacrifice_is_eligible(scope: WardSacrificeScope, row: ObjectQueryResult, *, payer: str) -> bool:
    if row.zone != "battlefield" or row.controller != payer or row.phased_out:
        return False
    if scope is WardSacrificeScope.CREATURE:
        return "creature" in row.types
    return scope is WardSacrificeScope.PERMANENT_WITH_POSITIVE_MANA_VALUE and row.mana_value >= 1
