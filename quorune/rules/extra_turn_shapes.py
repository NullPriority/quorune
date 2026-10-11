from __future__ import annotations

"""Closed extra-turn effect shapes over the canonical turn queue."""

from collections.abc import Mapping

from ..compiler.extra_turn_templates import FIXED_EXTRA_TURN_CAPABILITY, FIXED_EXTRA_TURN_MECHANIC


def fixed_extra_turn_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (FIXED_EXTRA_TURN_MECHANIC not in mechanic_ids or not isinstance(effects, (list, tuple))
            or not 1 <= len(effects) <= 10):
        return ()
    players = []
    for effect in effects:
        if (not isinstance(effect, Mapping) or set(effect) != {"op", "player", "source"}
                or effect.get("op") != "extra_turn" or effect.get("source") != "$source"
                or type(effect.get("player")) is not str
                or effect["player"] not in ("$controller", "$target.0")):
            return ()
        players.append(effect["player"])
    if len(set(players)) != 1:
        return ()
    targeted = players[0] == "$target.0"
    if not targeted:
        return (FIXED_EXTRA_TURN_CAPABILITY,) if target_schema is None else ()
    if (not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or target_schema not in (
        {"zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 1},
        {"zones": ["player"], "categories": ["player"], "player_relation": "opponent", "count": 1},
    )):
        return ()
    return (FIXED_EXTRA_TURN_CAPABILITY, "target.revalidate_resolution")


def is_closed_fixed_extra_turn_program(program):
    required = fixed_extra_turn_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)
