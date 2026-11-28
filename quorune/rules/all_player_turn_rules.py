"""Closed effect instructions for turn rules whose scope is every player."""

from collections.abc import Mapping

from ..all_player_turn_rule_model import (
    ALL_PLAYER_TURN_RULE_KIND, TURN_LAND_MANA_TRIGGER_CAPABILITY, AllPlayerTurnRule, TurnLandManaTriggerSpec,
)
from .effect_shape_walkers import nested_effects


CONTROLLER_TURN_HEXPROOF_CAPABILITY = "rule.player.turn_controller_hexproof"
ALL_PLAYER_TURN_RULE_MECHANIC = "turn-all-player-rules"
ALL_PLAYER_TURN_RULE_CAPABILITIES = {
    AllPlayerTurnRule.HEXPROOF: "rule.player.turn_all_players_hexproof",
    AllPlayerTurnRule.NO_LIFE_LOSS: "life.loss.prohibition.all_players_until_end_of_turn",
    AllPlayerTurnRule.NO_GAME_OUTCOME: "rule.player.turn_all_players_match_outcome_prohibition",
}


TURN_LAND_MANA_TRIGGER_VERSION = 3
TURN_LAND_MANA_TRIGGER_DEPENDENCIES = (TURN_LAND_MANA_TRIGGER_CAPABILITY,)


def turn_land_mana_trigger_instruction(spec, *, source="$stack"):
    return {"op": ALL_PLAYER_TURN_RULE_KIND, "schema_version": TURN_LAND_MANA_TRIGGER_VERSION,
            "source": source, "mana_trigger": spec.to_dict()}


def uses_turn_land_mana_trigger(effect):
    return effect.get("op") == ALL_PLAYER_TURN_RULE_KIND and (
        "mana_trigger" in effect or effect.get("schema_version") == TURN_LAND_MANA_TRIGGER_VERSION
    )


def decode_turn_land_mana_trigger(effect, *, source="$stack"):
    if (set(effect) != {"op", "schema_version", "source", "mana_trigger"}
            or type(effect["schema_version"]) is not int or effect["schema_version"] != TURN_LAND_MANA_TRIGGER_VERSION
            or effect["op"] != ALL_PLAYER_TURN_RULE_KIND or effect["source"] != source):
        raise ValueError("Turn mana trigger requires its fixed payload and resolving source")
    return TurnLandManaTriggerSpec.from_dict(effect["mana_trigger"])


def all_player_turn_rule_instruction(rule: AllPlayerTurnRule, source="$stack") -> dict:
    return {"op": ALL_PLAYER_TURN_RULE_KIND, "schema_version": 1, "source": source, "rule": rule.value}


def controller_turn_hexproof_instruction(*, source="$stack", player="$controller") -> dict:
    return {
        "op": ALL_PLAYER_TURN_RULE_KIND, "schema_version": 2,
        "source": source, "rule": AllPlayerTurnRule.HEXPROOF.value, "player": player,
    }


def decode_controller_turn_hexproof(effect, *, source="$stack", player="$controller") -> None:
    if (not isinstance(effect, Mapping) or type(effect.get("schema_version")) is not int
            or dict(effect) != controller_turn_hexproof_instruction(source=source, player=player)):
        raise ValueError("Controller hexproof requires its resolving spell and current controller")


def decode_all_player_turn_rule(effect: Mapping, source="$stack") -> AllPlayerTurnRule:
    if (
        not isinstance(effect, Mapping) or set(effect) != {"op", "schema_version", "source", "rule"}
        or effect["op"] != ALL_PLAYER_TURN_RULE_KIND
        or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
        or type(effect["source"]) is not str or not effect["source"] or effect["source"] != source
    ):
        raise ValueError("An all-player turn rule requires its complete instruction and resolving source")
    return AllPlayerTurnRule(effect["rule"])


def all_player_turn_rule_node_capabilities(*, effects, target_schema, mechanic_ids) -> tuple[str, ...]:
    if not effects or target_schema is not None or ALL_PLAYER_TURN_RULE_MECHANIC not in mechanic_ids:
        return ()
    if any(uses_turn_land_mana_trigger(effect) for effect in effects):
        try:
            if len(effects) != 1:
                return ()
            decode_turn_land_mana_trigger(effects[0])
        except (TypeError, ValueError):
            return ()
        return TURN_LAND_MANA_TRIGGER_DEPENDENCIES
    try:
        rules = tuple(decode_all_player_turn_rule(effect) for effect in effects)
    except (TypeError, ValueError):
        return ()
    if len(set(rules)) != len(rules):
        return ()
    return tuple(ALL_PLAYER_TURN_RULE_CAPABILITIES[rule] for rule in rules)


def all_player_turn_rule_runtime_dependencies(program) -> tuple[str, ...]:
    effects = tuple(effect for effect in nested_effects((*program.effects, *program.handlers))
                    if effect.get("op") == ALL_PLAYER_TURN_RULE_KIND)
    if not effects:
        return ()
    if (
        (program.event, program.active_zone) != ("resolve", "stack") or program.event_condition is not None
        or program.target_schema is not None or program.handlers
        or any(effect not in program.effects for effect in effects)
    ):
        raise ValueError("An all-player turn rule requires a direct unqualified spell resolution")
    if any(uses_turn_land_mana_trigger(effect) for effect in effects):
        if len(effects) != 1 or len(program.effects) != 1 or program.cost_schema is not None:
            raise ValueError("Turn mana trigger requires its exact untargeted spell")
        decode_turn_land_mana_trigger(effects[0])
        return TURN_LAND_MANA_TRIGGER_DEPENDENCIES
    dependencies = set()
    for effect in effects:
        if effect.get("schema_version") == 2:
            decode_controller_turn_hexproof(effect)
            dependencies.add(CONTROLLER_TURN_HEXPROOF_CAPABILITY)
        else:
            dependencies.add(ALL_PLAYER_TURN_RULE_CAPABILITIES[decode_all_player_turn_rule(effect)])
    return tuple(sorted(dependencies))


def is_closed_turn_land_mana_trigger_program(program):
    if not any(uses_turn_land_mana_trigger(effect) for effect in program.effects):
        return False
    try:
        required = all_player_turn_rule_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
