"""One player-loss effect delegated to the canonical elimination transaction."""
from dataclasses import dataclass
from .context import SemanticNodeError
from .intents import EliminatePlayersIntent, IntentPlan
from ..rules.event_outcome_shapes import PLAYER_LOSS_CAPABILITY


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


PLAYER_OUTCOME_HANDLERS = (LoseGameEffectHandler(),)
