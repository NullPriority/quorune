"""Closed effect instructions for turn rules whose scope is every player."""

from collections.abc import Mapping

from ..all_player_turn_rule_model import ALL_PLAYER_TURN_RULE_KIND, AllPlayerTurnRule
from .effect_shape_walkers import nested_effects


ALL_PLAYER_TURN_RULE_MECHANIC = "turn-all-player-rules"
ALL_PLAYER_TURN_RULE_CAPABILITIES = {
    AllPlayerTurnRule.HEXPROOF: "rule.player.turn_all_players_hexproof",
    AllPlayerTurnRule.NO_LIFE_LOSS: "life.loss.prohibition.all_players_until_end_of_turn",
    AllPlayerTurnRule.NO_GAME_OUTCOME: "rule.player.turn_all_players_match_outcome_prohibition",
}


def all_player_turn_rule_instruction(rule: AllPlayerTurnRule, source="$stack") -> dict:
    return {"op": ALL_PLAYER_TURN_RULE_KIND, "schema_version": 1, "source": source, "rule": rule.value}


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
    dependencies = tuple(sorted({
        ALL_PLAYER_TURN_RULE_CAPABILITIES[decode_all_player_turn_rule(effect)] for effect in effects
    }))
    return dependencies
