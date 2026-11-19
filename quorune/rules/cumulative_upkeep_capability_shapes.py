from __future__ import annotations

"""Closed capability shapes for fixed cumulative-upkeep triggers."""

from typing import Any, Iterable, Mapping, Sequence

from ..fixed_mana_abilities import MANA_COST_KEYS


def _is_controller_upkeep(
    event_condition: Mapping[str, Any] | None,
) -> bool:
    return dict(event_condition or {}) == {
        "all": [
            {
                "field": "player",
                "op": "eq",
                "value": "$source.controller",
            },
            {"field": "step", "op": "eq", "value": "upkeep"},
        ]
    }


def fixed_mana_cumulative_upkeep_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    event_condition: Mapping[str, Any] | None,
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return ownership only for one fixed-mana cumulative-upkeep trigger."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if "cumulative upkeep" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    cost = effect.get("cost_per_counter")
    if (
        target_schema is not None
        or set(effect) != {"op", "player", "source", "cost_per_counter"}
        or effect.get("op") != "cumulative_upkeep"
        or effect.get("player") != "$controller"
        or effect.get("source") != "$source"
        or not isinstance(cost, Mapping)
        or set(cost) != set(MANA_COST_KEYS)
        or any(type(amount) is not int or amount < 0 for amount in cost.values())
        or not any(cost.values())
        or not _is_controller_upkeep(event_condition)
    ):
        return ()
    return ("counter.producer.cumulative_upkeep_fixed_mana",)


def fixed_life_cumulative_upkeep_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    event_condition: Mapping[str, Any] | None,
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return ownership only for one fixed-life cumulative-upkeep trigger."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if "cumulative upkeep" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    if (
        target_schema is not None
        or set(effect) != {"op", "player", "source", "life_per_counter"}
        or effect.get("op") != "cumulative_upkeep_life"
        or effect.get("player") != "$controller"
        or effect.get("source") != "$source"
        or type(effect.get("life_per_counter")) is not int
        or effect["life_per_counter"] <= 0
        or not _is_controller_upkeep(event_condition)
    ):
        return ()
    return ("counter.producer.cumulative_upkeep_fixed_life",)


__all__ = [
    "fixed_life_cumulative_upkeep_node_capabilities",
    "fixed_mana_cumulative_upkeep_node_capabilities",
]


def cumulative_upkeep_covered_mechanics(supplied):
    """Keep fixed upkeep producers behind their existing counter/keyword coverage."""
    producers={
        'counter.producer.cumulative_upkeep_fixed_life',
        'counter.producer.cumulative_upkeep_fixed_mana',
        'counter.producer.cumulative_upkeep_fixed_objects',
        'counter.producer.cumulative_upkeep_counter_costs',
        'counter.producer.cumulative_upkeep_alternative_mana',
        'counter.producer.cumulative_upkeep_effect_costs',
        'counter.producer.cumulative_upkeep_graveyard_library',
    }
    return {'cr-122-counters','cumulative upkeep'} if producers.intersection(supplied) else set()
