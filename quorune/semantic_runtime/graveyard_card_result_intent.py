"""A direct graveyard-card exile with a sealed source and closed result."""
from collections.abc import Mapping
from dataclasses import dataclass

from .zone_move_intent import ExilePublicGraveyardCardIntent
from ..graveyard_card_result_model import GraveyardCardCounterLifeSpec


@dataclass(frozen=True, slots=True)
class GraveyardCardCounterLifeIntent(ExilePublicGraveyardCardIntent):
    source_object_id: str | None = None
    source_logical_object_id: str | None = None
    result: GraveyardCardCounterLifeSpec | None = None

    def __post_init__(self):
        super(GraveyardCardCounterLifeIntent, self).__post_init__()
        if any(type(v) is not str or not v for v in (self.actor, self.object_ref, self.reason)):
            raise ValueError('Targeted graveyard card result needs explicit actor, target and reason')
        if (self.source_object_id is None) != (self.source_logical_object_id is None):
            raise ValueError('Targeted graveyard card result source identities must be paired')
        if any(v is not None and (type(v) is not str or not v)
                for v in (self.source_object_id, self.source_logical_object_id)):
            raise ValueError('Targeted graveyard card result source identities must be nonempty or absent')
        if not isinstance(self.result, GraveyardCardCounterLifeSpec):
            raise ValueError('Targeted graveyard card result must retain its closed typed recipe')

    def identity(self):
        return {'actor': self.actor, 'card_ref': self.object_ref, 'reason': self.reason,
            'source_object_id': self.source_object_id, 'source_logical_object_id': self.source_logical_object_id,
            'result': self.result.to_dict()}


def validate_graveyard_card_counter_life_identity(value):
    expected = {'actor', 'card_ref', 'reason', 'source_object_id', 'source_logical_object_id', 'result'}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError('Targeted graveyard card result identity fields are closed')
    result = GraveyardCardCounterLifeSpec.from_dict(value['result'])
    intent = GraveyardCardCounterLifeIntent(actor=value['actor'], object_ref=value['card_ref'],
        reason=value['reason'], source_object_id=value['source_object_id'],
        source_logical_object_id=value['source_logical_object_id'], result=result)
    return intent.identity()
