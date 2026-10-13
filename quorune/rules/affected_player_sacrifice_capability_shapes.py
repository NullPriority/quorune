from __future__ import annotations

"""Capability shape for one fixed affected-player sacrifice instruction."""

from typing import Any, Iterable, Mapping, Sequence

from ..compiler.affected_player_sacrifice_templates import (
    FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY,
    FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC,
    fixed_affected_player_sacrifice_predicate_is_closed,
)


def is_closed_affected_player_sacrifice_program(program: Any) -> bool:
    required=fixed_affected_player_sacrifice_node_capabilities(effects=program.effects,
        target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)


def fixed_affected_player_sacrifice_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize one fixed affected-player permanent-sacrifice choice."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if not {
        FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC,
        "sacrifice",
    }.issubset(mechanics) or len(effects) != 1:
        return ()
    effect = effects[0]
    targeted = effect.get("players") == ["$target.0"]
    expected_fields = {
        "op",
        "actor",
        "players",
        "zone",
        "predicate",
        "count",
        "then",
        "prompt",
        *(("target",) if targeted else ()),
    }
    if (
        set(effect) != expected_fields
        or effect.get("op") != "choose_cards_apnap"
        or effect.get("actor") != "$controller"
        or effect.get("zone") != "battlefield"
        or effect.get("count") not in {1, 2}
        or effect.get("then") != "sacrifice"
        or type(effect.get("prompt")) is not str
        or not str(effect.get("prompt")).strip()
        or not isinstance(effect.get("predicate"), Mapping)
        or not fixed_affected_player_sacrifice_predicate_is_closed(
            effect["predicate"]
        )
    ):
        return ()
    dependencies = {
        FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY,
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
    elif target_schema is not None or effect.get("players") not in {
        "all",
        "opponents",
    }:
        return ()
    return tuple(sorted(dependencies))


__all__ = ["fixed_affected_player_sacrifice_node_capabilities"]
