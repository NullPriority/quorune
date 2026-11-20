"""Lower one owned graveyard instruction without reading hidden library order."""
from dataclasses import dataclass
from ..graveyard_recycling_model import (
    GraveyardRecyclingSpec,
    GRAVEYARD_RECYCLING_OPERATION, GRAVEYARD_RECYCLING_CAPABILITY,
)
from .context import SemanticNodeError
from .zone_move_intent import _freeze_replacement_selections


@dataclass(frozen=True, slots=True)
class GraveyardRecyclingIntent:
    actor: str
    player: str
    recycling: GraveyardRecyclingSpec
    reason: str
    replacement_selections: tuple = ()

    def __post_init__(self):
        if (not isinstance(self.recycling, GraveyardRecyclingSpec)
                or any(type(v) is not str or not v for v in (self.actor, self.player, self.reason))
                or self.recycling.scope == 'all_players' and self.player != 'all'
                or self.recycling.scope == 'controller' and self.player != self.actor):
            raise ValueError('Graveyard recycling intent needs its exact player relation')
        object.__setattr__(self, 'replacement_selections', _freeze_replacement_selections(
            self.replacement_selections, family='Graveyard recycling'))


@dataclass(frozen=True, slots=True)
class GraveyardRecyclingHandler:
    operation: str = GRAVEYARD_RECYCLING_OPERATION
    handler_id: str = 'generic.whole-graveyard-recycling.v1'
    schema_version: int = 1
    family: str = GRAVEYARD_RECYCLING_CAPABILITY
    rule_references: tuple[str, ...] = ('400.7', '608.2b', '701.24a', '701.24c', '701.24d', '903.9b')
    capability_dependencies: tuple[str, ...] = (GRAVEYARD_RECYCLING_CAPABILITY, 'zone.change.destination_replacement')

    def lower(self, effect, context):
        from .intents import IntentPlan
        if (set(effect) - {'op', 'recycling', 'player', 'reason', '_runtime_source', '_replacement_selections'}
                or not {'op', 'recycling', 'player'} <= set(effect) or effect['op'] != self.operation):
            raise SemanticNodeError('Graveyard recycling fields are incomplete or unknown')
        try:
            spec = GraveyardRecyclingSpec.from_dict(effect['recycling'])
            context.query.require_active_seat(context.actor)
            if spec.scope != 'all_players': context.query.require_active_seat(effect['player'])
            intent = GraveyardRecyclingIntent(context.actor, effect['player'], spec,
                str(effect.get('reason') or context.default_reason), effect.get('_replacement_selections', ()))
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))
