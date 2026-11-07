"""Closed resolution-time values and requested CR119.5 life transitions."""
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True,slots=True)
class LifeTotalValueSpec:
    kind: str
    total: int | None=None
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or type(self.kind) is not str or self.kind not in {
                    'fixed','starting','starting_half_rounded_up','highest','lowest','creatures','permanents','library',
                }
                or self.kind=='fixed' and (type(self.total) is not int or self.total<0)
                or self.kind!='fixed' and self.total is not None):
            raise ValueError('Life-total value requires its closed supported kind and fields')

    def to_dict(self):
        return {'schema_version':1,'kind':self.kind,**({'total':self.total} if self.kind=='fixed' else {})}

    @classmethod
    def from_dict(cls,value):
        fields={'schema_version','kind'}
        if isinstance(value,Mapping) and value.get('kind')=='fixed':fields.add('total')
        if not isinstance(value,Mapping) or set(value)!=fields:
            raise ValueError('Life-total values have a closed wire shape')
        return cls(value['kind'],value.get('total'),value['schema_version'])


@dataclass(frozen=True,slots=True)
class LifeTotalTransition:
    player: str
    before: int
    requested_total: int

    def __post_init__(self):
        if type(self.player) is not str or not self.player or any(type(value) is not int for value in (self.before,self.requested_total)):
            raise ValueError('A requested life-total transition requires exact player and integer totals')

    @property
    def delta(self):return self.requested_total-self.before


def plan_life_total_setting(value,players,life_totals,*,starting_life,counts=None):
    """Freeze every requested total before any replacement or life mutation."""
    if (not isinstance(value,LifeTotalValueSpec) or not isinstance(players,tuple) or not players
            or any(type(player) is not str for player in players) or len(set(players))!=len(players)
            or not isinstance(life_totals,Mapping) or not life_totals
            or any(type(player) is not str or not player or type(total) is not int for player,total in life_totals.items())
            or any(type(player) is not str or player not in life_totals for player in players)
            or type(starting_life) is not int or starting_life<1):
        raise ValueError('Life-total setting requires one complete live-player snapshot')
    if value.kind in {'creatures','permanents','library'} and (not isinstance(counts,Mapping)
            or set(counts)!=set(players) or any(type(count) is not int or count<0 for count in counts.values())):
        raise ValueError('Count-based life totals require exact known counts for every affected player')
    fixed={'fixed':value.total,'starting':starting_life,'starting_half_rounded_up':(starting_life + 1) // 2,
        'highest':max(life_totals.values()),'lowest':min(life_totals.values())}
    return tuple(LifeTotalTransition(player,life_totals[player],
        fixed[value.kind] if value.kind in fixed else counts[player]) for player in players)
