from __future__ import annotations

"""Closed initiative instructions and the affected player of inherent triggers."""

from collections.abc import Mapping
from dataclasses import dataclass

from .dungeon_progress import venture_node_capabilities


TAKE_INITIATIVE_FIELD = "take_initiative"
TAKE_INITIATIVE_CAPABILITY = "variant.initiative.take"
TAKE_INITIATIVE_MECHANIC = "cr-726-the-initiative"
INHERENT_INITIATIVE_TAKE = "builtin:initiative-take"
INHERENT_INITIATIVE_VENTURE = "builtin:initiative-venture"
INITIATIVE_TRIGGER_PLAYER = "initiative_trigger_player"


def initiative_effect_player(item, inherent_key: str) -> str:
    if item.semantic_key != inherent_key:
        return item.controller
    player = item.context.get(INITIATIVE_TRIGGER_PLAYER)
    if type(player) is not str or not player:
        raise ValueError("The inherent initiative ability lost its affected player")
    return player


def initiative_instruction(effect: Mapping, *, actor: str = "$controller") -> None:
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "player", TAKE_INITIATIVE_FIELD}
            or effect["op"] != "choose_option" or effect["player"] != actor):
        raise ValueError("Taking the initiative requires its closed player instruction")
    raw = effect[TAKE_INITIATIVE_FIELD]
    if (not isinstance(raw, Mapping) or set(raw) != {"schema_version"}
            or type(raw["schema_version"]) is not int or raw["schema_version"] != 1):
        raise ValueError("Taking the initiative requires its current instruction schema")


def initiative_payload_dependencies(effect: Mapping) -> tuple[str, ...]:
    if effect.get("op") != "choose_option" or TAKE_INITIATIVE_FIELD not in effect:
        return ()
    initiative_instruction(effect)
    return (TAKE_INITIATIVE_CAPABILITY,)


def initiative_node_capabilities(*, effects, target_schema, mechanic_ids=()) -> tuple[str, ...]:
    if target_schema is not None or len(effects) != 1 or TAKE_INITIATIVE_MECHANIC not in mechanic_ids:
        return ()
    try:
        return initiative_payload_dependencies(effects[0])
    except (ValueError, TypeError):
        return ()


def dungeon_choice_node_capabilities(*, effects, target_schema, mechanic_ids=()) -> tuple[str, ...]:
    return (venture_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids)
            or initiative_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids))


def is_closed_initiative_program(program) -> bool:
    required = initiative_node_capabilities(effects=program.effects, target_schema=program.target_schema,
                                            mechanic_ids=program.coverage)
    return bool(required) and set(required) <= set(program.capability_dependencies)


@dataclass(frozen=True, slots=True)
class TakeInitiativeIntent:
    player: str
    stack_ref: str

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value for value in (self.player, self.stack_ref)):
            raise ValueError("Taking the initiative requires a player and its resolving instruction")
