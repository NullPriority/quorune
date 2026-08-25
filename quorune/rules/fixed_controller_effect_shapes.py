from __future__ import annotations

"""Capability closure for fixed life and ordered controller effects."""

from typing import Any, Iterable, Mapping, Sequence

from .node_capability_shapes import (
    fixed_counter_placement_node_capabilities,
    fixed_draw_node_capabilities,
    fixed_scry_node_capabilities,
)
from .affected_player_discard_capability_shapes import (
    fixed_affected_player_discard_node_capabilities,
)


_FIXED_CONTROLLER_SEQUENCE_MECHANIC = "fixed-controller-effect-sequence"
_FIXED_COUNTER_CONTROLLER_SEQUENCE_MECHANIC = (
    "fixed-counter-controller-effect-sequence"
)
_LIFE_OPERATION = "life"


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def fixed_life_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return life ownership only for the closed fixed-value grammar."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if "cr-119-life" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    operation = effect.get("op")
    dependencies = {"life.change.effect"}
    any_player_schema = {
        "zones": ["player"],
        "categories": ["player"],
        "player_relation": "any",
        "count": 1,
    }
    opponent_schema = {
        **any_player_schema,
        "player_relation": "opponent",
    }
    actual_schema = dict(target_schema or {})
    if operation == _LIFE_OPERATION:
        player = effect.get("player")
        valid = (
            set(effect) == {"op", "player", "delta"}
            and player in {"$controller", "$target.0"}
            and _positive_int(effect.get("delta"))
            and (
                player == "$controller"
                or actual_schema == any_player_schema
            )
        )
        expected_schema = (
            None if player == "$controller" else any_player_schema
        )
    elif operation == "lose_life":
        player = effect.get("player")
        valid = (
            set(effect) == {"op", "player", "amount"}
            and player in {"$controller", "$target.0"}
            and _positive_int(effect.get("amount"))
            and (
                player == "$controller"
                or actual_schema in (any_player_schema, opponent_schema)
            )
        )
        expected_schema = (
            None if player == "$controller" else actual_schema
        )
    elif operation == "lose_life_each_opponent":
        valid = (
            set(effect) == {"op", "amount"}
            and _positive_int(effect.get("amount"))
            and "cr-101-the-magic-golden-rules" in mechanics
        )
        expected_schema = None
    elif operation == "drain_opponent":
        valid = (
            set(effect) == {"op", "target", "amount"}
            and effect.get("target") == "$target.0"
            and _positive_int(effect.get("amount"))
        )
        expected_schema = opponent_schema
    elif operation == "drain_each_opponent":
        valid = (
            set(effect) == {"op", "amount"}
            and _positive_int(effect.get("amount"))
            and "cr-101-the-magic-golden-rules" in mechanics
        )
        expected_schema = None
    else:
        valid = False
        expected_schema = None
    if not valid or dict(target_schema or {}) != dict(expected_schema or {}):
        return ()
    if target_schema is not None:
        if "cr-115-targets" not in mechanics:
            return ()
        dependencies.add("target.revalidate_resolution")
    return tuple(sorted(dependencies))


def fixed_controller_effect_sequence_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Own exactly two ordered controller effects containing one draw."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        _FIXED_CONTROLLER_SEQUENCE_MECHANIC not in mechanics
        or target_schema is not None
        or len(effects) != 2
    ):
        return ()
    dependencies: set[str] = {"resolution.effect_sequence.fixed_controller"}
    draw_count = 0
    for effect in effects:
        operation = effect.get("op")
        if operation == "draw":
            required = fixed_draw_node_capabilities(
                effects=(effect,),
                target_schema=None,
                mechanic_ids=("cr-121-drawing-a-card",),
            )
            draw_count += 1
        elif operation == "choose_cards_apnap":
            if effect.get("players") != ["$controller"]:
                return ()
            required = fixed_affected_player_discard_node_capabilities(
                effects=(effect,),
                target_schema=None,
                mechanic_ids=(
                    "fixed-affected-player-discard",
                    "cr-402-hand",
                ),
                allow_controller=True,
            )
        elif operation in {_LIFE_OPERATION, "lose_life"}:
            required = fixed_life_node_capabilities(
                effects=(effect,),
                target_schema=None,
                mechanic_ids=("cr-119-life",),
            )
        elif operation == "scry":
            required = fixed_scry_node_capabilities(
                effects=(effect,),
                target_schema=None,
                mechanic_ids=("scry",),
            )
        else:
            return ()
        if not required:
            return ()
        dependencies.update(required)
    if draw_count != 1:
        return ()
    return tuple(sorted(dependencies))


def fixed_counter_controller_effect_sequence_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Own one fixed counter placement and one fixed controller effect."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        _FIXED_COUNTER_CONTROLLER_SEQUENCE_MECHANIC not in mechanics
        or len(effects) != 2
    ):
        return ()
    counter_effects = tuple(
        effect for effect in effects if effect.get("op") == "place_counters"
    )
    controller_effects = tuple(
        effect for effect in effects if effect.get("op") != "place_counters"
    )
    if len(counter_effects) != 1 or len(controller_effects) != 1:
        return ()
    counter_effect = counter_effects[0]
    if counter_effect.get("card") == "$source.zone_object":
        if target_schema is not None:
            return ()
        normalized_counter = {**counter_effect, "card": "$source"}
    elif counter_effect.get("card") == "$target.0":
        normalized_counter = counter_effect
    else:
        return ()
    counter_dependencies = fixed_counter_placement_node_capabilities(
        effects=(normalized_counter,),
        target_schema=target_schema,
        mechanic_ids=mechanics,
    )
    if not counter_dependencies:
        return ()
    controller_effect = controller_effects[0]
    operation = controller_effect.get("op")
    if operation == "draw":
        controller_dependencies = fixed_draw_node_capabilities(
            effects=(controller_effect,),
            target_schema=None,
            mechanic_ids=("cr-121-drawing-a-card",),
        )
    elif operation in {_LIFE_OPERATION, "lose_life"}:
        controller_dependencies = fixed_life_node_capabilities(
            effects=(controller_effect,),
            target_schema=None,
            mechanic_ids=("cr-119-life",),
        )
    elif operation == "scry":
        controller_dependencies = fixed_scry_node_capabilities(
            effects=(controller_effect,),
            target_schema=None,
            mechanic_ids=("scry",),
        )
    else:
        return ()
    if not controller_dependencies:
        return ()
    return tuple(
        sorted(
            {
                "resolution.effect_sequence.fixed_counter_controller",
                *counter_dependencies,
                *controller_dependencies,
            }
        )
    )


__all__ = [
    "fixed_counter_controller_effect_sequence_node_capabilities",
    "fixed_controller_effect_sequence_node_capabilities",
    "fixed_life_node_capabilities",
]
