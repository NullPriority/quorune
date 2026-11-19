"""Typed simultaneous graveyard exile with a committed creature result."""

from dataclasses import dataclass

from ..targeted_graveyard_outcome_model import TargetedGraveyardExileOutcomeSpec
from ..zone_trigger_events import ZoneTransitionKind
from .intents import MoveObjectsSimultaneouslyIntent


@dataclass(frozen=True, slots=True)
class TargetedGraveyardExileOutcomeIntent(MoveObjectsSimultaneouslyIntent):
    result: TargetedGraveyardExileOutcomeSpec | None = None

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
        if not self.result.scope.minimum <= len(self.object_refs) <= self.result.scope.maximum:
            raise ValueError('Targeted graveyard exile references exceed their exact target bounds')
