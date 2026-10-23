from __future__ import annotations

"""Closed reductions of already matched, current layer-5 public rows."""

import math
from typing import Any, Mapping, Sequence

from .characteristic_fragments import CharacteristicQuantityReduction
from .object_query import ObjectQueryResult
from .mana_symbol_counts import colored_mana_symbol_count
from .counter_snapshot import permanent_counter_total


def reduce_public_quantity(
    rows: Sequence[tuple[ObjectQueryResult, Mapping[str, Any], str]],
    reduction: CharacteristicQuantityReduction,
    *, mana_colors: tuple[str, ...] = (),
) -> int:
    if reduction is CharacteristicQuantityReduction.OBJECT_COUNT:
        return len(rows)
    if reduction is CharacteristicQuantityReduction.TOTAL_COUNTERS:
        return sum(permanent_counter_total(row.counters) for row, _data, _kind in rows)
    if reduction in {CharacteristicQuantityReduction.COLORED_MANA_SYMBOLS,CharacteristicQuantityReduction.DEVOTION}:
        count = 0
        for _row, data, _kind in rows:
            if "mana_cost" not in data or (data["mana_cost"] is not None and type(data["mana_cost"]) is not str):
                raise ValueError("A matched mana cost is unavailable")
            count += colored_mana_symbol_count(data['mana_cost'],mana_colors)
        return count
    if reduction in {
        CharacteristicQuantityReduction.MAXIMUM_MANA_VALUE,
        CharacteristicQuantityReduction.TOTAL_MANA_VALUE,
    }:
        values = []
        for _row, data, _kind in rows:
            value = data.get("mana_value")
            if type(value) not in {int, float} or not math.isfinite(value) or value < 0 or int(value) != value:
                raise ValueError("A matched public mana value is unavailable or unsupported")
            values.append(int(value))
        return max(values, default=0) if reduction is CharacteristicQuantityReduction.MAXIMUM_MANA_VALUE else sum(values)
    terms: set[str] = set()
    for row, _data, kind in rows:
        if reduction is CharacteristicQuantityReduction.DISTINCT_CARD_TYPES:
            if kind == "card":
                terms.update(row.types)
        elif reduction is CharacteristicQuantityReduction.DISTINCT_BASIC_LAND_TYPES:
            terms.update(set(row.subtypes) & {"plains", "island", "swamp", "mountain", "forest"})
        elif reduction is CharacteristicQuantityReduction.DISTINCT_COLORS:
            if not set(row.colors) <= {"W", "U", "B", "R", "G"}:
                raise ValueError("A matched color is unavailable or unsupported")
            terms.update(row.colors)
        else:
            raise ValueError("Unsupported public collection reduction")
    return len(terms)
