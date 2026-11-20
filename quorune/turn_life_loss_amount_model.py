"""An independently versioned total of actual current-turn player life losses."""
from collections.abc import Mapping
from dataclasses import dataclass
from .query_effect_amount_model import PublicQueryAmountError

TURN_LIFE_LOSS_TOTAL_KIND='all_player_turn_life_loss_amount'
TURN_LIFE_LOSS_TOTAL_CAPABILITY='quantity_expression.all_player_turn_life_loss'
TURN_LIFE_LOSS_TOTAL_MECHANIC='all-player-turn-life-loss-amount'


@dataclass(frozen=True,slots=True)
class AllPlayerTurnLifeLossSpec:
    schema_version: int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1:
            raise PublicQueryAmountError('All-player turn life loss requires its explicit version')

    def to_dict(self):return {'kind':TURN_LIFE_LOSS_TOTAL_KIND,'schema_version':1}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'kind','schema_version'}
                or value.get('kind')!=TURN_LIFE_LOSS_TOTAL_KIND):
            raise PublicQueryAmountError('All-player turn life-loss fields are closed')
        return cls(value['schema_version'])
