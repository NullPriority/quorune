from __future__ import annotations

"""Capability shape for one fixed affected-player discard instruction."""

from typing import Any, Iterable, Mapping, Sequence

from ..compiler.affected_player_discard_templates import (
    FIXED_AFFECTED_PLAYER_DISCARD_CAPABILITY,
    FIXED_AFFECTED_PLAYER_DISCARD_MECHANIC,
    fixed_affected_player_discard_predicate_is_closed,
)


def fixed_affected_player_discard_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
    allow_controller: bool = False,
) -> tuple[str, ...]:
    """Recognize one fixed affected-player private discard choice."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if not {
        FIXED_AFFECTED_PLAYER_DISCARD_MECHANIC,
        "cr-402-hand",
    }.issubset(mechanics) or len(effects) != 1:
        return ()
    effect = effects[0]
    targeted = effect.get("players") == ["$target.0"]
    controller = effect.get("players") == ["$controller"]
    if controller and not allow_controller:
        return ()
    expected_fields = {
        "op",
        "actor",
        "players",
        "zone",
        "predicate",
        "count",
        "then",
        "hidden",
        "prompt",
        *(("target",) if targeted else ()),
    }
    if (
        set(effect) != expected_fields
        or effect.get("op") != "choose_cards_apnap"
        or effect.get("actor") != "$controller"
        or effect.get("zone") != "hand"
        or effect.get("count") not in (
            {1, 2, 3, 4} if controller else {1, 2, 3}
        )
        or effect.get("then") != "discard"
        or effect.get("hidden") is not True
        or type(effect.get("prompt")) is not str
        or not str(effect.get("prompt")).strip()
        or not isinstance(effect.get("predicate"), Mapping)
        or not fixed_affected_player_discard_predicate_is_closed(
            effect["predicate"]
        )
    ):
        return ()
    dependencies = {
        FIXED_AFFECTED_PLAYER_DISCARD_CAPABILITY,
        "zone.change.destination_replacement",
    }
    if targeted:
        if (
            effect.get("target") != "$target.0"
            or "cr-115-targets" not in mechanics
            or dict(target_schema or {})
            not in (
                {
                    "zones": ["player"],
                    "categories": ["player"],
                    "player_relation": "any",
                    "count": 1,
                },
                {
                    "zones": ["player"],
                    "categories": ["player"],
                    "player_relation": "opponent",
                    "count": 1,
                },
            )
        ):
            return ()
        dependencies.add("target.revalidate_resolution")
    elif target_schema is not None:
        return ()
    elif controller and effect.get("players") != ["$controller"]:
        return ()
    elif not controller and effect.get("players") not in {"all", "opponents"}:
        return ()
    return tuple(sorted(dependencies))


__all__ = ["fixed_affected_player_discard_node_capabilities"]
