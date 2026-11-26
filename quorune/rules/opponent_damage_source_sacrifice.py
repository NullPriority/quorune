"""Bind one sacrifice to the opposing source's role in a committed damage event."""

from __future__ import annotations

from collections.abc import Mapping

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import nested_effects
from .optional_counter_capability_shapes import opponent_damage_to_controller_condition


OPPONENT_DAMAGE_SOURCE_SACRIFICE_KIND = "opponent_damage_source_sacrifice_player_set"
OPPONENT_DAMAGE_SOURCE_SACRIFICE_CAPABILITY = "resolution.event_player.opponent_damage_source_sacrifice"
OPPONENT_DAMAGE_SOURCE_SACRIFICE_VARIANT = "opponent_damage_source_sacrifice"
OPPONENT_DAMAGE_SOURCE_SACRIFICE_MECHANICS = (
    "fixed-affected-player-sacrifice", "sacrifice", "fixed-event-player-sacrifice",
)
_CONTROLLED_ZONES = frozenset({"battlefield", "stack"})
_OTHER_CARD_ZONES = frozenset({"graveyard", "exile", "command", "hand", "library"})


def opponent_damage_source_player_set():
    return {"kind": OPPONENT_DAMAGE_SOURCE_SACRIFICE_KIND, "schema_version": 1}


def _base_effect():
    from ..compiler.affected_player_sacrifice_templates import (
        AffectedPlayerSacrificeSubject, FixedAffectedPlayerSacrificeTemplate,
    )

    template = FixedAffectedPlayerSacrificeTemplate(
        AffectedPlayerSacrificeSubject.CONTROLLER, 1, ObjectQuerySpec(zones=("battlefield",)),
    )
    return template.effects[0]


def opponent_damage_source_sacrifice_effect():
    return {**_base_effect(), "players": opponent_damage_source_player_set()}


def is_opponent_damage_source_sacrifice(effect):
    players = effect.get("players") if isinstance(effect, Mapping) else None
    return isinstance(players, Mapping) and players.get("kind") == OPPONENT_DAMAGE_SOURCE_SACRIFICE_KIND


def opponent_damage_source_sacrifice_node_capabilities(*, effects, target_schema):
    if (target_schema is not None or len(effects) != 1 or not is_opponent_damage_source_sacrifice(effects[0])):
        return ()
    effect = thaw_value(FrozenMap(effects[0]))
    if (type(effect.get("count")) is not int or type(effect["players"].get("schema_version")) is not int
            or effect != opponent_damage_source_sacrifice_effect()):
        return ()
    from .affected_player_sacrifice_capability_shapes import fixed_affected_player_sacrifice_node_capabilities

    owned = fixed_affected_player_sacrifice_node_capabilities(
        effects=(_base_effect(),), target_schema=None, mechanic_ids=OPPONENT_DAMAGE_SOURCE_SACRIFICE_MECHANICS,
    )
    return tuple(sorted({
        OPPONENT_DAMAGE_SOURCE_SACRIFICE_CAPABILITY, "resolution.event_player.fixed_sacrifice",
        "trigger.event.normalized_damage", *owned,
    })) if owned else ()


def opponent_damage_source_sacrifice_runtime_dependencies(program):
    if not any(is_opponent_damage_source_sacrifice(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = opponent_damage_source_sacrifice_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    if (not required or (program.event, program.active_zone) != ("damage.dealt", "battlefield")
            or thaw_value(program.event_condition) != opponent_damage_to_controller_condition()
            or program.cost_schema is not None or program.handlers or program.trigger_limit is not None):
        raise ValueError("Opponent-source sacrifice requires each exact committed damage occurrence")
    return required


def resolve_opponent_damage_source_players(host, value, item):
    if (not isinstance(value, Mapping) or type(value.get("schema_version")) is not int
            or dict(value) != opponent_damage_source_player_set()):
        raise ValueError("Opponent-source sacrifice requires its exact affected-player reference")
    context = item.context
    source_zone = context.get("source_zone")
    damaged_player = context.get("target")
    if (context.get("target_kind") != "player" or type(damaged_player) is not str
            or damaged_player not in host.state.players
            or type(context.get("amount")) is not int or context["amount"] <= 0
            or source_zone not in _CONTROLLED_ZONES | _OTHER_CARD_ZONES):
        raise ValueError("Opponent-source sacrifice requires committed damage to its original player")
    recipient = context.get("source_controller" if source_zone in _CONTROLLED_ZONES else "source_owner")
    if type(recipient) is not str or recipient == damaged_player or recipient not in host.state.players:
        raise ValueError("Opponent-source sacrifice requires its original opposing source participant")
    return [recipient] if host.state.players[recipient].in_game else []
