from __future__ import annotations

"""Closed capability shapes for fixed positive damage instructions."""

from typing import Any, Iterable, Mapping, Sequence

from ..compiler.direct_target import DirectPermanentTargetSpec
from ..fixed_damage_set_model import (
    FixedDamageSetError,
    FixedDamageSetSpec,
    PermanentControllerRelation,
    PermanentDamageGroup,
    PlayerDamageGroup,
)
from .permanent_predicate_capability_shapes import (
    direct_permanent_target_schema_is_closed,
    direct_target_predicate_capabilities,
)


_FIXED_DAMAGE_TARGET_SCHEMAS: dict[str, Mapping[str, Any]] = {
    "any_target": {
        "zones": ["player", "battlefield"],
        "categories": ["player", "permanent"],
        "predicate": "damageable",
        "count": 1,
    },
    "creature": {
        "zones": ["battlefield"],
        "categories": ["permanent"],
        "types_any": ["creature"],
        "count": 1,
    },
    "creature_or_planeswalker": {
        "zones": ["battlefield"],
        "categories": ["permanent"],
        "types_any": ["creature", "planeswalker"],
        "count": 1,
    },
    "player_or_planeswalker": {
        "zones": ["player", "battlefield"],
        "categories": ["player", "permanent"],
        "predicate": "player_or_planeswalker",
        "count": 1,
    },
    "opponent_or_planeswalker": {
        "zones": ["player", "battlefield"],
        "categories": ["player", "permanent"],
        "predicate": "player_or_planeswalker",
        "count": 1,
        "player_relation": "opponent",
    },
    "player": {
        "zones": ["player"],
        "categories": ["player"],
        "count": 1,
    },
    "opponent": {
        "zones": ["player"],
        "categories": ["player"],
        "count": 1,
        "player_relation": "opponent",
    },
}
_PLAYER_DAMAGE_DOMAINS = frozenset(
    {
        "any_target",
        "player_or_planeswalker",
        "opponent_or_planeswalker",
        "player",
        "opponent",
    }
)
_PERMANENT_DAMAGE_DOMAINS = frozenset(
    {
        "any_target",
        "creature",
        "creature_or_planeswalker",
        "player_or_planeswalker",
        "opponent_or_planeswalker",
    }
)


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _fixed_set_damage_capabilities(
    effect: Mapping[str, Any],
    target_schema: Mapping[str, Any] | None,
    mechanics: set[str],
) -> tuple[str, ...] | None:
    if not (
        set(effect) == {"op", "source", "amount", "groups"}
        and effect.get("op") == "damage_fixed_set"
        and effect.get("source") == "$source"
        and _positive_int(effect.get("amount"))
    ):
        return None
    try:
        spec = FixedDamageSetSpec.from_dict({"groups": effect.get("groups")})
    except FixedDamageSetError:
        return ()
    targeted = any(
        isinstance(group, PermanentDamageGroup)
        and group.controller_relation is PermanentControllerRelation.TARGET_PLAYER
        for group in spec.groups
    )
    player_targets = (
        {
            "zones": ["player"],
            "categories": ["player"],
            "count": 1,
        },
        {
            "zones": ["player"],
            "categories": ["player"],
            "player_relation": "opponent",
            "count": 1,
        },
    )
    valid_target = (
        not targeted and target_schema is None
        or targeted and dict(target_schema or {}) in player_targets
    )
    if not valid_target or targeted and "cr-115-targets" not in mechanics:
        return ()
    dependencies = {"damage.amount.positive", "damage.batch.fixed_set"}
    if any(isinstance(group, PlayerDamageGroup) for group in spec.groups):
        dependencies.add("damage.result.player_life")
    permanent_groups = tuple(
        group for group in spec.groups if isinstance(group, PermanentDamageGroup)
    )
    if permanent_groups:
        dependencies.add("damage.result.multitype_permanent")
    if targeted:
        dependencies.add("target.revalidate_resolution")
    if any(_uses_characteristic_predicate(group) for group in permanent_groups):
        dependencies.add("target.permanent.characteristic_predicate")
    if any(group.query.state_predicate is not None for group in permanent_groups):
        dependencies.add("state_query.permanent.public_state_predicate")
    return tuple(sorted(dependencies))


def _uses_characteristic_predicate(group: PermanentDamageGroup) -> bool:
    query = group.query
    return bool(
        query.excluded_types
        or query.subtypes_any
        or query.excluded_subtypes
        or query.supertypes_all
        or query.colors_all
        or query.colors_any
        or query.colorless is not None
        or query.minimum_color_count is not None
        or query.keywords_all
        or query.keywords_none
        or query.token is not None
        or group.exclude_source
    )


def _fixed_direct_damage_capabilities(
    effect: Mapping[str, Any],
    target_schema: Mapping[str, Any] | None,
    mechanics: set[str],
) -> tuple[str, ...]:
    if (
        "cr-115-targets" not in mechanics
        or set(effect) != {"op", "source", "target", "amount"}
        or effect.get("op") != "damage"
        or effect.get("source") != "$source"
        or effect.get("target") != "$target.0"
        or not _positive_int(effect.get("amount"))
    ):
        return ()
    schema = dict(target_schema or {})
    domain = next(
        (
            name
            for name, expected in _FIXED_DAMAGE_TARGET_SCHEMAS.items()
            if schema == expected
        ),
        None,
    )
    direct_target = None
    if domain is None and direct_permanent_target_schema_is_closed(schema):
        direct_target = DirectPermanentTargetSpec.from_target_schema(schema)
    if domain is None and direct_target is None:
        return ()
    dependencies = {"damage.amount.positive"}
    if domain in _PLAYER_DAMAGE_DOMAINS:
        dependencies.add("damage.result.player_life")
    if domain in _PERMANENT_DAMAGE_DOMAINS or direct_target is not None:
        dependencies.add("damage.result.multitype_permanent")
    if direct_target is not None:
        dependencies.update(direct_target_predicate_capabilities(schema))
        dependencies.add("target.revalidate_resolution")
    else:
        dependencies.add(
            "target.public.player_or_damageable_permanent"
            if domain == "any_target"
            else "target.revalidate_resolution"
        )
    return tuple(sorted(dependencies))


def fixed_damage_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for the closed fixed-damage vocabulary."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if "cr-120-damage" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    fixed_set = _fixed_set_damage_capabilities(
        effect, target_schema, mechanics
    )
    if fixed_set is not None:
        return fixed_set
    if (
        target_schema is None
        and set(effect) == {"op", "source", "amount"}
        and effect.get("op") == "damage_each_opponent"
        and effect.get("source") == "$source"
        and _positive_int(effect.get("amount"))
    ):
        return ("damage.amount.positive", "damage.result.player_life")
    return _fixed_direct_damage_capabilities(effect, target_schema, mechanics)


__all__ = ["fixed_damage_node_capabilities"]
