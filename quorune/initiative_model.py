from __future__ import annotations

"""Closed initiative instructions and the affected player of inherent triggers."""

from collections.abc import Mapping
from dataclasses import dataclass

from .dungeon_catalog import DungeonId
from .dungeon_progress import DUNGEON_VENTURE_CAPABILITY, venture_node_capabilities


TAKE_INITIATIVE_FIELD = "take_initiative"
TAKE_INITIATIVE_CAPABILITY = "variant.initiative.take"
TAKE_INITIATIVE_MECHANIC = "cr-726-the-initiative"
INHERENT_INITIATIVE_TAKE = "builtin:initiative-take"
INHERENT_INITIATIVE_VENTURE = "builtin:initiative-venture"
INITIATIVE_TRIGGER_PLAYER = "initiative_trigger_player"

DUNGEON_COMPLETED_FACT = "controller_completed_a_dungeon"
DUNGEON_NOT_COMPLETED_FACT = "controller_has_not_completed_named_dungeon"
INITIATIVE_HOLDER_FACT = "controller_has_the_initiative"
DUNGEON_STATUS_FACTS = {
    DUNGEON_COMPLETED_FACT: DUNGEON_VENTURE_CAPABILITY,
    DUNGEON_NOT_COMPLETED_FACT: DUNGEON_VENTURE_CAPABILITY,
    INITIATIVE_HOLDER_FACT: TAKE_INITIATIVE_CAPABILITY,
}


def validate_dungeon_status_fact(fact: str, *, amount, parameter, schema_version) -> None:
    if (fact not in DUNGEON_STATUS_FACTS or type(schema_version) is not int or schema_version != 4
            or type(amount) is not int or amount != 1):
        raise ValueError("A dungeon status condition requires one current boolean fact")
    if fact == DUNGEON_NOT_COMPLETED_FACT:
        if type(parameter) is not str or parameter not in {dungeon.value for dungeon in DungeonId}:
            raise ValueError("Named dungeon history requires a known dungeon")
    elif parameter is not None:
        raise ValueError("This dungeon status condition has no named parameter")


def dungeon_status_dependencies(value) -> tuple[str, ...]:
    if not isinstance(value, Mapping) or value.get("kind") != "public_fact_at_least":
        return ()
    fact = value.get("fact")
    if type(fact) is not str or fact not in DUNGEON_STATUS_FACTS:
        return ()
    validate_dungeon_status_fact(fact, amount=value.get("amount"), parameter=value.get("fact_parameter"),
                                 schema_version=value.get("schema_version"))
    return (DUNGEON_STATUS_FACTS[fact],)


def dungeon_condition_dependencies(condition) -> tuple[str, ...]:
    return tuple(sorted({capability for leaf in condition.leaf_conditions()
                         for capability in dungeon_status_dependencies(leaf.to_dict())}))


def dungeon_status_value(state, controller: str, fact: str, parameter: str | None) -> int | None:
    if fact == INITIATIVE_HOLDER_FACT:
        return int(getattr(state, "initiative", None) == controller)
    journey = getattr(state.players[controller], "dungeon_journey", None)
    if fact == DUNGEON_COMPLETED_FACT:
        return int(journey is not None and journey.completed > 0)
    if journey is None:
        return 1
    counts = dict(journey.completed_by_dungeon)
    if counts.get(DungeonId(parameter), 0):
        return 0
    return 1 if sum(counts.values()) == journey.completed else None


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
