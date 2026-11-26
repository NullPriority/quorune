"""Typed simultaneous graveyard exile with a committed creature result."""

from dataclasses import dataclass

from ..targeted_graveyard_outcome_model import TargetedGraveyardExileOutcomeSpec
from ..zone_trigger_events import ZoneTransitionKind
from .intents import MoveObjectsSimultaneouslyIntent


@dataclass(frozen=True, slots=True)
class TargetedGraveyardExileOutcomeIntent(MoveObjectsSimultaneouslyIntent):
    result: TargetedGraveyardExileOutcomeSpec | None = None
    damaged_player: str | None = None
    damage_target_bound: int | None = None

    def __post_init__(self) -> None:
        MoveObjectsSimultaneouslyIntent.__post_init__(self)
        if not isinstance(self.result, TargetedGraveyardExileOutcomeSpec):
            raise ValueError('Targeted graveyard exile requires a typed outcome recipe')
        if (
            self.expected_zones != ('graveyard',)
            or self.destination != 'exile'
            or self.transition_kind is not ZoneTransitionKind.ORDINARY
            or self.owned_only
            or self.controlled_only
        ):
            raise ValueError('Targeted graveyard outcomes require ordinary public graveyard exile')
        if self.result.damage_linked:
            if (type(self.damaged_player) is not str or not self.damaged_player or self.damaged_player.startswith('$')
                    or type(self.damage_target_bound) is not int or self.damage_target_bound < 1):
                raise ValueError('Damage-linked graveyard exile requires its committed player and damage')
            maximum = self.damage_target_bound
        else:
            if self.damaged_player is not None or self.damage_target_bound is not None:
                raise ValueError('Fixed graveyard exile cannot carry a damage-linked target scope')
            maximum = self.result.scope.maximum
        if not self.result.scope.minimum <= len(self.object_refs) <= maximum:
            raise ValueError('Targeted graveyard exile references exceed their exact target bounds')
