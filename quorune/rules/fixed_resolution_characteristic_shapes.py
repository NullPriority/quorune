from __future__ import annotations

"""Capability closure for fixed resolution-locked characteristic sets."""

from typing import Any, Iterable, Mapping, Sequence

from ..compiler.fixed_resolution_characteristic_queries import (
    fixed_resolution_characteristic_query_is_closed,
)
from ..keyword_abilities import (
    FIXED_CHARACTERISTIC_KEYWORDS,
)
from ..object_predicate import ObjectQueryError, ObjectQuerySpec


FIXED_RESOLUTION_CHARACTERISTICS_CAPABILITY = (
    "continuous.resolution.fixed_characteristics_until_end_of_turn"
)


def fixed_resolution_characteristic_set_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Own one fixed, resolution-locked public characteristic set."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        "cr-611-continuous-effects" not in mechanics
        or len(effects) != 1
    ):
        return ()
    effect = effects[0]
    has_keywords = "keywords" in effect
    expected_fields = {"op", "predicate", "power", "toughness"}
    if has_keywords:
        expected_fields.add("keywords")
    if (
        set(effect) != expected_fields
        or effect.get("op")
        != "modify_all_matching_permanents_until_end_of_turn"
        or type(effect.get("power")) is not int
        or type(effect.get("toughness")) is not int
        or not isinstance(effect.get("predicate"), Mapping)
    ):
        return ()
    try:
        query = ObjectQuerySpec.from_dict(effect["predicate"])
    except (ObjectQueryError, TypeError):
        return ()
    if (
        dict(effect["predicate"]) != query.to_dict()
        or not fixed_resolution_characteristic_query_is_closed(
            query,
            target_schema=target_schema,
        )
    ):
        return ()
    if has_keywords:
        raw_keywords = effect["keywords"]
        if (
            not isinstance(raw_keywords, list)
            or not raw_keywords
            or any(type(keyword) is not str for keyword in raw_keywords)
            or len(set(raw_keywords)) != len(raw_keywords)
            or any(
                keyword not in FIXED_CHARACTERISTIC_KEYWORDS
                for keyword in raw_keywords
            )
        ):
            return ()
        required_keyword_mechanics = {
            keyword.casefold() for keyword in raw_keywords
        }
        if (
            not required_keyword_mechanics.issubset(mechanics)
            or mechanics.intersection(
                {
                    "continuous.ability.fixed_query_keyword_grant",
                    "continuous.power_toughness.fixed_anthem",
                }
            )
        ):
            return ()
    dependencies = {FIXED_RESOLUTION_CHARACTERISTICS_CAPABILITY}
    if query.state_predicate is not None:
        dependencies.add("state_query.permanent.public_state_predicate")
    if target_schema is not None:
        if "cr-115-targets" not in mechanics:
            return ()
        dependencies.add("target.revalidate_resolution")
    return tuple(sorted(dependencies))


__all__ = [
    "FIXED_RESOLUTION_CHARACTERISTICS_CAPABILITY",
    "fixed_resolution_characteristic_set_node_capabilities",
]
