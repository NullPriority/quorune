from __future__ import annotations

"""Typed player speed and source-free trigger vocabulary from CR 702.179."""

from dataclasses import dataclass
from typing import Mapping

SPEED_CAPABILITY='variant.speed.player'
START_ENGINES_HANDLER='ability.static.start-your-engines.v1'
SPEED_OPERATION='increase_speed'
SPEED_KEY='speed'
SPEED_TRIGGER_TURN_KEY='speed_trigger_turn'


def player_speed(player):
    value=getattr(player,'stats',{}).get(SPEED_KEY)
    if value is not None and (type(value) is not int or value<0):raise ValueError('Player speed must be absent or a nonnegative integer')
    return value


@dataclass(frozen=True,slots=True)
class SpeedIncrease:
    player: str
    amount: int
    if_less_than: int|None=None

    def __post_init__(self):
        if type(self.player) is not str or not self.player or type(self.amount) is not int or self.amount<1:raise ValueError('Speed increase requires a player and positive fixed amount')
        if self.if_less_than is not None and (type(self.if_less_than) is not int or self.if_less_than<1):raise ValueError('Speed increase limit is malformed')

    @classmethod
    def from_effect(cls,value):
        fields={'op','player','amount','if_less_than'}
        if not isinstance(value,Mapping) or set(value)!=fields or value['op']!=SPEED_OPERATION:raise ValueError('Speed instruction fields are malformed')
        return cls(value['player'],value['amount'],value['if_less_than'])
