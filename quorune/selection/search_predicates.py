"""Evaluate one closed hidden-search predicate against a supplied card row."""

from __future__ import annotations

from typing import Any, Mapping

from ..errors import GameRuleError
from ..library_search_model import mana_cost_search_predicate_matches
from ..object_predicate import ObjectQuerySpec
from ..object_query import object_matches_query


def search_leaf_matches(row, record, query: ObjectQuerySpec, selector: Mapping[str, Any]) -> bool:
    if not object_matches_query(row, query):
        return False
    names = {
        str(value).casefold() for value in selector.get("names") or []
    }
    if names and record.name.casefold() not in names:
        return False
    mana_value = selector.get("mana_value")
    if mana_value is not None:
        constraint = (
            dict(mana_value)
            if isinstance(mana_value, Mapping)
            else {"equal": mana_value}
        )
        if 'equal' in constraint and constraint['equal'] is None:
            # CR701.23c: an undefined quality permits searching, but no
            # card can be found. It must not become an unrestricted query.
            return False
        if (
            constraint.get("equal") is not None
            and row.mana_value != float(constraint["equal"])
        ):
            return False
        if (
            constraint.get("minimum") is not None
            and row.mana_value < float(constraint["minimum"])
        ):
            return False
        if (
            constraint.get("maximum") is not None
            and row.mana_value > float(constraint["maximum"])
        ):
            return False
    predicate = selector.get("predicate")
    if predicate in {None, ""}:
        return True
    if predicate == "noncreature":
        return "creature" not in row.types
    if predicate == "instant_or_sorcery":
        return bool(set(row.types).intersection({"instant", "sorcery"}))
    mana_cost_match = mana_cost_search_predicate_matches(
        predicate,
        record,
        zone=row.zone,
    )
    if mana_cost_match is not None:
        return mana_cost_match
    if predicate == "land_with_basic_land_type":
        return (
            "land" in row.types
            and bool(
                set(row.subtypes).intersection(
                    {"plains", "island", "swamp", "mountain", "forest"}
                )
            )
        )
    raise GameRuleError(f"Unsupported search predicate {predicate!r}")
