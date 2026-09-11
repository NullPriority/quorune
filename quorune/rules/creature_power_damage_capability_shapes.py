from __future__ import annotations

"""Strict capability shape for Fight and fixed creature-power damage."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..creature_power_damage_model import (
    CREATURE_POWER_DAMAGE_CAPABILITY,
    CREATURE_POWER_DAMAGE_LKI_CONTEXT,
    CREATURE_POWER_DAMAGE_MECHANIC,
    CREATURE_POWER_DAMAGE_OPERATION,
)


_ATTACHED_CREATURE = {
    "kind": "source_attachment",
    "relation": "enchanted_object",
    "required_card_type": "creature",
    "schema_version": 1,
}


def _creature_group(group: object, *, group_id: str) -> bool:
    if not isinstance(group, Mapping):
        return False
    value = dict(group)
    if value.pop("id", None) != group_id:
        return False
    if value.pop("zones", None) != ["battlefield"]:
        return False
    if value.pop("categories", None) != ["permanent"]:
        return False
    if value.pop("types_any", None) != ["creature"]:
        return False
    if value.pop("controller_relation", None) not in {
        "any",
        "opponent",
        "you",
    }:
        return False
    count = value.pop("count", None)
    minimum = value.pop("min", None)
    maximum = value.pop("max", None)
    if not (
        (count == 1 and minimum is None and maximum is None)
        or (count is None and minimum == 0 and maximum == 1)
    ):
        return False
    colors = value.pop("colors_any", None)
    if colors is not None and colors != ("G",) and colors != ["G"]:
        return False
    subtypes = value.pop("subtypes_any", None)
    if subtypes is not None and (
        not isinstance(subtypes, (list, tuple))
        or len(subtypes) != 1
        or type(subtypes[0]) is not str
        or not subtypes[0]
    ):
        return False
    state = value.pop("state_predicate", None)
    if state is not None and state != {
        "kind": "counter_minimum",
        "counter_name": "+1/+1",
        "minimum": 1,
    }:
        return False
    combat = value.pop("combat_state", None)
    if combat not in {None, "attacking", "blocking"}:
        return False
    exclusion = value.pop("source_exclusion", None)
    return exclusion in {None, True} and not value


def _recipient_group(group: object, *, direct: bool) -> bool:
    if not isinstance(group, Mapping):
        return False
    value = dict(group)
    if value.get("id") != "recipient":
        return False
    different = value.pop("different_from_groups", None)
    if (
        different is not None
        and different != ("fighter",)
        and different != ["fighter"]
    ):
        return False
    if different is not None and not direct:
        return False
    source_exclusion = value.pop("source_exclusion", None)
    if source_exclusion not in {None, True} or (
        source_exclusion is not None and direct
    ):
        return False
    if value == {
        "id": "recipient",
        "zones": ["player", "battlefield"],
        "categories": ["player", "permanent"],
        "predicate": "damageable",
        "count": 1,
    }:
        return True
    if value.get("types_any") == ["creature", "planeswalker"]:
        return set(value) == {
            "id",
            "zones",
            "categories",
            "types_any",
            "controller_relation",
            "count",
        } and value.get("zones") == ["battlefield"] and value.get(
            "categories"
        ) == ["permanent"] and value.get("controller_relation") in {
            "any",
            "opponent",
        } and value.get("count") == 1
    return _creature_group(value, group_id="recipient")


def _source_is_closed(source: object) -> bool:
    return source == "$source" or source == _ATTACHED_CREATURE


def fixed_creature_power_damage_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize only the compiler-owned Fight and bite instruction shape."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if not {
        CREATURE_POWER_DAMAGE_MECHANIC,
        "cr-115-targets",
        "cr-120-damage",
    }.issubset(mechanics) or len(effects) not in {1, 2}:
        return ()
    effect = effects[-1]
    if set(effect) != {
        "op",
        "kind",
        "source",
        "target",
        "target_must_be_creature",
        "source_lki",
    } or effect.get("op") != CREATURE_POWER_DAMAGE_OPERATION:
        return ()
    kind = effect.get("kind")
    must_be_creature = effect.get("target_must_be_creature")
    if kind not in {"bite", "fight"} or type(must_be_creature) is not bool:
        return ()
    if kind == "fight" and must_be_creature is not True:
        return ()
    schema = dict(target_schema or {})
    if set(schema) - {"groups", "globally_distinct"}:
        return ()
    groups = schema.get("groups")
    if not isinstance(groups, (list, tuple)):
        return ()
    source = effect.get("source")
    target = effect.get("target")
    lki = effect.get("source_lki")
    direct = source == "$target.0"
    prep_dependency: str | None = None
    if len(effects) == 2:
        prep = effects[0]
        if not direct:
            return ()
        if set(prep) == {"op", "card", "power", "toughness"} and (
            prep.get("op") == "modify_stats_until_end_of_turn"
            and prep.get("card") == "$target.0"
            and type(prep.get("power")) is int
            and type(prep.get("toughness")) is int
            and (prep.get("power"), prep.get("toughness")) != (0, 0)
            and "cr-611-continuous-effects" in mechanics
        ):
            prep_dependency = (
                "continuous.resolution.fixed_characteristics_until_end_of_turn"
            )
        elif set(prep) == {"op", "card", "counter", "amount", "source"} and (
            prep.get("op") == "place_counters"
            and prep.get("card") == "$target.0"
            and prep.get("counter") == "+1/+1"
            and type(prep.get("amount")) is int
            and prep.get("amount", 0) > 0
            and prep.get("source") == "$source"
            and "cr-122-counters" in mechanics
        ):
            prep_dependency = "counter.producer.fixed_effect"
        else:
            return ()
    if direct:
        if lki is not None or target != "$target.1" or len(groups) != 2:
            return ()
        if not _creature_group(groups[0], group_id="fighter"):
            return ()
    else:
        if not _source_is_closed(source) or target != "$target.0" or len(groups) != 1:
            return ()
        expected_lki = (
            "$context." + CREATURE_POWER_DAMAGE_LKI_CONTEXT
            if source == "$source"
            else None
        )
        if lki != expected_lki:
            return ()
    target_group = groups[-1]
    if kind == "fight":
        if "fight" not in mechanics or not _creature_group(
            target_group,
            group_id="opponent",
        ):
            return ()
        if (direct and schema.get("globally_distinct") is not True) or (
            not direct and "globally_distinct" in schema
        ):
            return ()
    else:
        if "globally_distinct" in schema or not _recipient_group(
            target_group,
            direct=direct,
        ):
            return ()
    dependencies = {
        CREATURE_POWER_DAMAGE_CAPABILITY,
        "damage.amount.positive",
        "damage.result.multitype_permanent",
        "target.revalidate_resolution",
    }
    if prep_dependency is not None:
        dependencies.add(prep_dependency)
    if kind == "bite" and isinstance(target_group, Mapping) and target_group.get(
        "predicate"
    ) == "damageable":
        dependencies.add("damage.result.player_life")
    return tuple(sorted(dependencies))


def fixed_creature_power_damage_covered_mechanics(
    capability_ids: Iterable[str],
) -> tuple[str, ...]:
    if CREATURE_POWER_DAMAGE_CAPABILITY not in set(capability_ids):
        return ()
    return (CREATURE_POWER_DAMAGE_MECHANIC, "fight")


__all__ = [
    "fixed_creature_power_damage_covered_mechanics",
    "fixed_creature_power_damage_node_capabilities",
]
