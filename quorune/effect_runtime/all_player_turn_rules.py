"""Commit a global turn rule through the canonical continuous-effect journal."""

from ..all_player_turn_rule_model import ResolvedAllPlayerTurnRule
from ..continuous_effect_state import ResolutionEffectSource, commit_continuous_effect
from ..errors import GameRuleError
from ..rules.all_player_turn_rules import decode_all_player_turn_rule


def apply_all_player_turn_rule(host, effect, *, actor, operation, reason):
    del operation, reason
    host._require_seat(actor, in_game=True)
    try:
        source = ResolutionEffectSource.from_effect(effect)
        instruction = {key: value for key, value in effect.items() if key not in {"_runtime_source", "reason"}}
        rule = decode_all_player_turn_rule(instruction, source.stack_ref)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    resolved = ResolvedAllPlayerTurnRule(host._next_ref("PR"), source.stack_ref, host._next_zone_timestamp(), rule)
    commit_continuous_effect(host.state, resolved)
    host._log(actor, "rule.all_players.turn.created", "A turn rule applies to all players.",
              {"effect": resolved.effect_id, "rule": rule.value}, importance=1)
    return resolved.effect_id
