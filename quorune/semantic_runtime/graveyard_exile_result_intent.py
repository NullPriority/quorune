"""A selected graveyard incarnation with one closed post-movement result."""
from collections.abc import Mapping
from dataclasses import dataclass

from .intents import MoveObjectsSimultaneouslyIntent
from ..graveyard_exile_result_model import GraveyardExileLifeResultSpec
from ..zone_trigger_events import ZoneTransitionKind


@dataclass(frozen=True, slots=True)
class GraveyardExileLifeResultIntent(MoveObjectsSimultaneouslyIntent):
    selected_identity: str = ''
    result: GraveyardExileLifeResultSpec | None = None

    def __post_init__(self):
        super(GraveyardExileLifeResultIntent, self).__post_init__()
        if (len(self.object_refs) != 1 or self.expected_zones != ('graveyard',)
                or self.destination != 'exile' or not self.owned_only or self.controlled_only
                or self.transition_kind is not ZoneTransitionKind.ORDINARY
                or self.battlefield_controller is not None or self.battlefield_tapped
                or self.library_order is not None):
            raise ValueError('Selected graveyard result requires one owned physical card exile')
        if type(self.selected_identity) is not str or not self.selected_identity:
            raise ValueError('Selected graveyard result requires its exact selected incarnation')
        if not isinstance(self.result, GraveyardExileLifeResultSpec):
            raise ValueError('Selected graveyard result requires its closed typed consequence')

    def identity(self):
        return {'actor': self.actor, 'card_ref': self.object_refs[0], 'selected_identity': self.selected_identity,
            'result': self.result.to_dict(), 'reason': self.reason}


def validate_graveyard_exile_life_result_identity(value):
    fields = {'actor', 'card_ref', 'selected_identity', 'result', 'reason'}
    if (not isinstance(value, Mapping) or set(value) != fields
            or any(type(value[k]) is not str or not value[k] for k in fields - {'result'})):
        raise ValueError('Selected graveyard result replacement identity is closed')
    result = GraveyardExileLifeResultSpec.from_dict(value['result'])
    return {**dict(value), 'result': result.to_dict()}
