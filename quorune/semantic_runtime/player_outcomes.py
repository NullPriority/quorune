"""Delegate typed win and loss effects to the canonical match outcome owners."""
from dataclasses import dataclass
from .context import SemanticNodeError
from .intents import EliminatePlayersIntent, IntentPlan, WinGameIntent
from ..rules.event_outcome_shapes import PLAYER_LOSS_CAPABILITY
from ..rules.upkeep_game_win import PLAYER_WIN_CAPABILITY
from ..rules.match_outcome_restrictions import (
    MATCH_OUTCOME_RESTRICTION_CAPABILITY, MATCH_OUTCOME_RESTRICTION_HANDLER, MatchOutcomeRestrictionSpec,
)


@dataclass(frozen=True, slots=True)
class LoseGameEffectHandler:
    handler_id: str = 'generic.lose-game-effect.v1'
    schema_version: int = 1
    family: str = 'effect.player-game-loss'
    operation: str = 'lose_game'
    rule_references: tuple[str,...] = ('104.3e','800.4a')
    capability_dependencies: tuple[str,...] = (PLAYER_LOSS_CAPABILITY,)

    def lower(self, effect, context):
        player = effect.get('player')
        if (set(effect) != {'op','player'} or effect.get('op') != self.operation
                or type(player) is not str or not player):
            raise SemanticNodeError('Player game loss requires one resolved known player')
        context.query.require_known_seat(player)
        return IntentPlan(operation=self.operation, handler_id=self.handler_id,
            intents=(EliminatePlayersIntent(actor=context.actor, players=(player,), reason=context.default_reason),))


@dataclass(frozen=True, slots=True)
class WinGameEffectHandler:
    handler_id: str = "generic.win-game-effect.v1"
    schema_version: int = 1
    family: str = "effect.player-game-win"
    operation: str = "win_game"
    rule_references: tuple[str, ...] = ("101.2", "104.2b")
    capability_dependencies: tuple[str, ...] = (PLAYER_WIN_CAPABILITY,)

    def lower(self, effect, context):
        player = effect.get("player")
        if set(effect) != {"op", "player"} or effect.get("op") != self.operation or player != context.actor:
            raise SemanticNodeError("A game win requires its resolving controller")
        context.query.require_known_seat(player)
        return IntentPlan(operation=self.operation, handler_id=self.handler_id,
                          intents=(WinGameIntent(actor=context.actor, player=player),))


PLAYER_OUTCOME_HANDLERS = (LoseGameEffectHandler(), WinGameEffectHandler())


@dataclass(frozen=True, slots=True)
class MatchOutcomeRestrictionHandler:
    handler_id: str = MATCH_OUTCOME_RESTRICTION_HANDLER
    schema_version: int = 1
    family: str = "rule.player.static_match_outcome_restriction"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = ("101.2", "104.2", "104.3", "301.5", "613.1f")
    capability_dependencies: tuple[str, ...] = (MATCH_OUTCOME_RESTRICTION_CAPABILITY,)

    def validate(self, descriptor):
        try:
            return MatchOutcomeRestrictionSpec.from_descriptor(descriptor)
        except (ValueError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def may_affect_static_components(self, descriptor):
        return False

    def lower(self, descriptor, context):
        del context
        self.validate(descriptor)
        return ()


def apply_turn_match_outcome_protection(host, actor, effect):
    from ..continuous_effect_state import ResolutionEffectSource, commit_continuous_effect
    from ..errors import GameRuleError
    from ..rules.turn_match_outcome_protection import validate_turn_match_outcome_protection
    from ..turn_match_outcome_model import ResolvedTurnMatchOutcomeProtection

    if actor not in host.active_seats:
        raise GameRuleError("Turn outcome protection requires a live resolving controller")
    try:
        source = ResolutionEffectSource.from_effect(effect).stack_ref
        bound = {key: value for key, value in effect.items() if key not in {"_runtime_source", "reason"}}
        validate_turn_match_outcome_protection(bound, actor=actor, source=source)
        rule = ResolvedTurnMatchOutcomeProtection(host._next_ref("OP"), source,
                                                  host._next_zone_timestamp(), actor)
        commit_continuous_effect(host.state, rule)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    host._log(actor, "rule.outcome.protection.created", "Turn outcome protection is active.",
              {"effect": rule.effect_id, "controller": actor}, importance=1)
    return rule.effect_id
