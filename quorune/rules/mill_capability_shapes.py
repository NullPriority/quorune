from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ..compiler.mill_templates import MILL_CAPABILITY_ID, MILL_MECHANIC_ID,EVENT_MILL_RECIPIENT_CAPABILITY,EVENT_MILL_RECIPIENT_MECHANIC


def _player_target_schema_is_closed(
    target_schema: Mapping[str, object] | None,
) -> bool:
    if not isinstance(target_schema, Mapping):
        return False
    required = {
        "zones": ["player"],
        "categories": ["player"],
        "count": 1,
    }
    if any(
        target_schema.get(key) != value
        for key, value in required.items()
    ):
        return False
    fields = set(target_schema)
    return fields in (
        set(required),
        {*required, "player_relation"},
    ) and target_schema.get("player_relation", "any") in {
        "any",
        "opponent",
    }


def fixed_mill_node_capabilities(
    *,
    effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize one mandatory fixed-count controller or direct-player Mill."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if MILL_MECHANIC_ID not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    if set(effect) == {"op", "scope", "count"}:
        if (
            effect.get("op") == "mill"
            and effect.get("scope") in {"all_active_players","controller_opponents"}
            and type(effect.get("count")) is int
            and int(effect["count"]) > 0
            and target_schema is None
        ):
            return (MILL_CAPABILITY_ID,)
        return ()
    if (
        set(effect) != {"op", "player", "count"}
        or effect.get("op") != "mill"
        or type(effect.get("count")) is not int
        or int(effect["count"]) <= 0
    ):
        return ()
    player = effect.get("player")
    if target_schema is None and EVENT_MILL_RECIPIENT_MECHANIC in mechanics and player in {
            '$context.target','$context.player','$context.defending_player'}:
        return (MILL_CAPABILITY_ID,EVENT_MILL_RECIPIENT_CAPABILITY)
    if target_schema is None and player == "$controller":
        return (MILL_CAPABILITY_ID,)
    if (
        "cr-115-targets" in mechanics
        and player == "$target.0"
        and _player_target_schema_is_closed(target_schema)
    ):
        return (MILL_CAPABILITY_ID, "target.revalidate_resolution")
    return ()


def mill_covered_mechanics(supplied):
    return {mechanic for cap,mechanic in ((MILL_CAPABILITY_ID,MILL_MECHANIC_ID),
        (EVENT_MILL_RECIPIENT_CAPABILITY,EVENT_MILL_RECIPIENT_MECHANIC)) if cap in supplied}


__all__ = ["fixed_mill_node_capabilities","mill_covered_mechanics"]
