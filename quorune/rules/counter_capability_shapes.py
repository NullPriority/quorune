from __future__ import annotations

"""Strict capability shape for fixed multi-subject counter placement."""

from typing import Any, Iterable, Mapping, Sequence

from ..keyword_counters import keyword_counter_mechanic
from .node_capability_shapes import fixed_counter_target_schema_is_closed


def fixed_counter_placement_group_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return ownership for one fixed same-kind multi-subject placement."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if not {"cr-122-counters", "cr-115-targets"}.issubset(mechanics):
        return ()
    if len(effects) != 1:
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "cards", "counter", "amount", "source"}
        or effect.get("op") != "place_counters"
        or effect.get("source") != "$source"
        or type(effect.get("counter")) is not str
        or not effect.get("counter")
        or effect.get("counter")
        != " ".join(str(effect.get("counter")).casefold().split())
        or type(effect.get("amount")) is not int
        or effect.get("amount", 0) <= 0
    ):
        return ()
    cards = effect.get("cards")
    if not isinstance(cards, (list, tuple)) or not 2 <= len(cards) <= 3:
        return ()
    if any(type(value) is not str or not value for value in cards):
        return ()
    source_count = cards.count("$source.zone_object")
    target_refs = tuple(
        value for value in cards if value != "$source.zone_object"
    )
    if (
        source_count > 1
        or not target_refs
        or target_refs
        != tuple(f"$target.{index}" for index in range(len(target_refs)))
    ):
        return ()

    schema = dict(target_schema or {})
    if set(schema) - {"groups", "globally_distinct"}:
        return ()
    raw_groups = schema.get("groups")
    if not isinstance(raw_groups, (list, tuple)) or len(raw_groups) != len(
        target_refs
    ):
        return ()
    if "globally_distinct" in schema and (
        schema["globally_distinct"] is not True or len(raw_groups) < 2
    ):
        return ()
    for index, raw_group in enumerate(raw_groups):
        if not isinstance(raw_group, Mapping):
            return ()
        group = dict(raw_group)
        if group.pop("id", None) != f"target_{index}":
            return ()
        count = group.pop("count", None)
        minimum = group.pop("min", None)
        maximum = group.pop("max", None)
        if count == 1 and minimum is None and maximum is None:
            pass
        elif (
            count is None
            and minimum == 0
            and maximum == 1
            and index == len(raw_groups) - 1
        ):
            pass
        else:
            return ()
        if not fixed_counter_target_schema_is_closed(
            {**group, "count": 1},
            allow_commander=True,
        ):
            return ()

    counter_mechanic = keyword_counter_mechanic(effect.get("counter"))
    if counter_mechanic is not None and counter_mechanic not in mechanics:
        return ()
    return (
        "counter.producer.fixed_permanent_group_effect",
        "counter.producer.fixed_effect",
        *(("counter.characteristic.keyword",) if counter_mechanic else ()),
        "target.revalidate_resolution",
    )


__all__ = ["fixed_counter_placement_group_node_capabilities"]
