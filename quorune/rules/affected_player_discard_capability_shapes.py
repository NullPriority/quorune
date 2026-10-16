from __future__ import annotations

"""Capability shape for one fixed affected-player discard instruction."""

from typing import Any, Iterable, Mapping, Sequence

from ..compiler.affected_player_discard_templates import (
    FIXED_AFFECTED_PLAYER_DISCARD_CAPABILITY,
    FIXED_AFFECTED_PLAYER_DISCARD_MECHANIC,
    fixed_affected_player_discard_predicate_is_closed,
    FIXED_CONTROLLER_DISCARD_MECHANIC,
    FIXED_CONTROLLER_DISCARD_CAPABILITY,
    FIXED_CONTEXT_PLAYER_DISCARD_MECHANIC,
    FIXED_CONTEXT_PLAYER_DISCARD_CAPABILITY,
)


def _literal_discard_count_is_closed(value, controller):
    return type(value) is int and value in ({1, 2, 3, 4} if controller else {1, 2, 3})


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
        or not _literal_discard_count_is_closed(effect.get("count"), controller)
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
    elif not controller and effect.get("players") not in ("all", "opponents"):
        return ()
    return tuple(sorted(dependencies))


def fixed_controller_discard_node_capabilities(*, effects, target_schema, mechanic_ids):
    if FIXED_CONTROLLER_DISCARD_MECHANIC not in set(mechanic_ids) or len(effects) != 1:
        return ()
    effect = effects[0]
    if effect.get("players") != ["$controller"] or type(effect.get("count")) is not int:
        return ()
    required = fixed_affected_player_discard_node_capabilities(
        effects=effects, target_schema=target_schema,
        mechanic_ids=mechanic_ids, allow_controller=True,
    )
    return tuple(sorted({*required, FIXED_CONTROLLER_DISCARD_CAPABILITY})) if required else ()


def fixed_context_player_discard_node_capabilities(*, effects, target_schema, mechanic_ids):
    if FIXED_CONTEXT_PLAYER_DISCARD_MECHANIC not in set(mechanic_ids) or len(effects) != 1 or target_schema is not None:
        return ()
    effect = effects[0]
    if effect.get("players") not in (["$context.player"], ["$context.target"]) or type(effect.get("count")) is not int or effect.get("count") not in {1, 2, 3}:
        return ()
    restored = {**effect, "players": ["$controller"]}
    required = fixed_affected_player_discard_node_capabilities(
        effects=(restored,), target_schema=None,
        mechanic_ids=mechanic_ids, allow_controller=True,
    )
    return tuple(sorted({*required, FIXED_CONTEXT_PLAYER_DISCARD_CAPABILITY})) if required else ()


def is_closed_fixed_bound_discard_program(program):
    """Admit only a closed producer whose occurrence defines its player slot."""
    arguments = dict(effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=program.coverage)
    required = fixed_controller_discard_node_capabilities(**arguments)
    if required:
        return set(required).issubset(program.capability_dependencies)
    required = fixed_context_player_discard_node_capabilities(**arguments)
    if not required or not set(required).issubset(program.capability_dependencies):
        return False

    def guarantees(condition, field, value):
        if not isinstance(condition, Mapping):
            return False
        if condition == {"field": field, "op": "eq", "value": value}:
            return True
        children = condition.get("all", ())
        return isinstance(children, (list, tuple)) and any(guarantees(child, field, value) for child in children)

    players = program.effects[0]["players"]
    if players == ["$context.target"]:
        return program.event in {"damage.dealt", "damage.dealt.self"} and guarantees(program.event_condition, "target_kind", "player")
    return program.event == "step.begin" and guarantees(program.event_condition, "step", "upkeep")


def is_closed_fixed_affected_player_discard_program(program):
    required=fixed_affected_player_discard_node_capabilities(effects=program.effects,
        target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)


__all__ = ["fixed_affected_player_discard_node_capabilities", "fixed_controller_discard_node_capabilities", "fixed_context_player_discard_node_capabilities", "is_closed_fixed_bound_discard_program"]
