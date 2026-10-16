from __future__ import annotations

"""CR 702.131 public designation values, separate from player counters."""

from dataclasses import dataclass

CITYS_BLESSING_KEY='citys_blessing'
CITYS_BLESSING_CAPABILITY='player.designation.citys_blessing'
ASCEND_STATIC_HANDLER='ability.static.ascend.v1'


def player_has_citys_blessing(player) -> bool:
    value=getattr(player,'stats',{}).get(CITYS_BLESSING_KEY,False)
    if type(value) is not bool:raise ValueError('City blessing must be a boolean player designation')
    return value


@dataclass(frozen=True,slots=True)
class AscendIntent:
    player: str
    reason: str

    def __post_init__(self):
        if any(type(value) is not str or not value for value in (self.player,self.reason)):
            raise ValueError('Ascend requires one player and an instruction reason')
