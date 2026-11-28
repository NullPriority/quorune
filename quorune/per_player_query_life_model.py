"""Sealed current-seat life-loss changes owned by the life transaction."""

from collections.abc import Mapping
from dataclasses import dataclass

from .life_state import LifeChange
from .query_effect_amount_model import PublicQueryAmountError
from .per_player_query_life_spec import (
    BOUND_PER_PLAYER_QUERY_LIFE_KIND,
    OPPONENT_ACTUAL_TURN_LOSS_VERSION,
    OPPONENT_POISON_THRESHOLD_LIFE_VERSION,
    OPPONENT_SUBTYPE_ABSENCE_LIFE_VERSION,
    PER_PLAYER_QUERY_LIFE_CAPABILITY,
    PER_PLAYER_QUERY_LIFE_KIND,
    PER_PLAYER_QUERY_LIFE_MECHANIC,
    PerPlayerQueryLifeLossSpec,
    PlayerLifeLossRelation,
    PoisonCounterThresholdLifeLoss,
    TurnLifeLossHistory,
    unbound_per_player_query_life_spec,
    validate_per_player_life_loss_instruction,
)

_RUNTIME_FIELDS = frozenset({"reason", "source", "cause", "_runtime_source", "_replacement_selections"})


def per_player_query_life_spec(effect, *, runtime=False):
    if not runtime:
        return unbound_per_player_query_life_spec(effect)
    if not isinstance(effect, Mapping):
        raise ValueError("Per-player life loss requires a complete instruction")
    spec = BoundPerPlayerLifeLoss.from_dict(effect.get("amount"))
    validate_per_player_life_loss_instruction(effect, spec.relation, extra_fields=_RUNTIME_FIELDS)
    return spec

@dataclass(frozen=True,slots=True)
class BoundPerPlayerLifeLoss:
    controller: str
    relation: PlayerLifeLossRelation
    changes: tuple[LifeChange,...]
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or type(self.controller) is not str or not self.controller
                or not isinstance(self.relation,PlayerLifeLossRelation)
                or not isinstance(self.changes,tuple)
                or any(not isinstance(c,LifeChange) or type(c.player) is not str or not c.player
                    or type(c.amount) is not int or c.amount>=0 for c in self.changes)
                or len({c.player for c in self.changes})!=len(self.changes)
                or self.relation is PlayerLifeLossRelation.OPPONENTS and any(c.player==self.controller for c in self.changes)):
            raise PublicQueryAmountError('Bound per-player loss requires distinct player identities and negative canonical changes')

    @property
    def total_loss(self):return -sum(c.amount for c in self.changes)

    def to_dict(self):
        return {'kind':BOUND_PER_PLAYER_QUERY_LIFE_KIND,'schema_version':1,'controller':self.controller,
            'relation':self.relation.value,'changes':[{'player':c.player,'amount':c.amount} for c in self.changes]}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'kind','schema_version','controller','relation','changes'}
                or value['kind']!=BOUND_PER_PLAYER_QUERY_LIFE_KIND or not isinstance(value['changes'],(list,tuple))
                or any(not isinstance(c,Mapping) or set(c)!={'player','amount'} for c in value['changes'])):
            raise PublicQueryAmountError('Bound per-player life-loss fields are closed')
        return cls(value['controller'],PlayerLifeLossRelation(value['relation']),
            tuple(LifeChange(c['player'],c['amount']) for c in value['changes']),value['schema_version'])
