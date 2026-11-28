"""The public battlefield criteria for a typed Ward sacrifice."""

from __future__ import annotations

from ..object_query import ObjectQueryResult
from ..trigger_participation import WardSacrificeScope


def ward_sacrifice_is_eligible(scope: WardSacrificeScope, row: ObjectQueryResult, *, payer: str) -> bool:
    if row.zone != "battlefield" or row.controller != payer or row.phased_out or row.sacrifice_prohibited:
        return False
    if scope is WardSacrificeScope.TWO_PERMANENTS:
        return True
    if scope is WardSacrificeScope.THREE_NONLAND_PERMANENTS:
        return "land" not in row.types
    if scope is WardSacrificeScope.CREATURE:
        return "creature" in row.types
    if scope is WardSacrificeScope.FOOD_ARTIFACT:
        return "artifact" in row.types and "food" in row.subtypes
    return scope is WardSacrificeScope.PERMANENT_WITH_POSITIVE_MANA_VALUE and row.mana_value >= 1
