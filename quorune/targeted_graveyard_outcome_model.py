"""Closed target scopes and outcomes for committed graveyard card exile."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class GraveyardExileTargetScope(StrEnum):
    ONE = 'one'
    UP_TO_ONE = 'up_to_one'
    UP_TO_TWO = 'up_to_two'
    UP_TO_TWO_SAME_GRAVEYARD = 'up_to_two_same_graveyard'

    @property
    def minimum(self) -> int:
        return 1 if self is self.ONE else 0

    @property
    def maximum(self) -> int:
        return 2 if self in (self.UP_TO_TWO, self.UP_TO_TWO_SAME_GRAVEYARD) else 1

    @property
    def same_graveyard(self) -> bool:
        return self is self.UP_TO_TWO_SAME_GRAVEYARD


class CreatureExileOutcome(StrEnum):
    DRAIN_TWO_ONCE = 'drain_two_once'
    GAIN_ONE_PER_CREATURE = 'gain_one_per_creature'
    CREATE_ONE_CLUE = 'create_one_clue'
    CREATE_ONE_FOOD = 'create_one_food'


_OUTCOMES_BY_SCOPE = {
    GraveyardExileTargetScope.ONE: frozenset((CreatureExileOutcome.CREATE_ONE_FOOD,)),
    GraveyardExileTargetScope.UP_TO_ONE: frozenset((
        CreatureExileOutcome.DRAIN_TWO_ONCE, CreatureExileOutcome.CREATE_ONE_CLUE,
    )),
    GraveyardExileTargetScope.UP_TO_TWO: frozenset((CreatureExileOutcome.GAIN_ONE_PER_CREATURE,)),
    GraveyardExileTargetScope.UP_TO_TWO_SAME_GRAVEYARD: frozenset((CreatureExileOutcome.DRAIN_TWO_ONCE,)),
}


@dataclass(frozen=True, slots=True)
class TargetedGraveyardExileOutcomeSpec:
    scope: GraveyardExileTargetScope
    outcome: CreatureExileOutcome
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Targeted graveyard exile outcome requires schema version one')
        if not isinstance(self.scope, GraveyardExileTargetScope) or not isinstance(self.outcome, CreatureExileOutcome):
            raise ValueError('Targeted graveyard exile requires a typed scope and outcome')
        if self.outcome not in _OUTCOMES_BY_SCOPE[self.scope]:
            raise ValueError('Targeted graveyard exile scope and outcome are not supported together')

    def to_dict(self) -> dict[str, str | int]:
        return {'schema_version': self.schema_version, 'scope': self.scope.value, 'outcome': self.outcome.value}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> 'TargetedGraveyardExileOutcomeSpec':
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'scope', 'outcome'}:
            raise ValueError('Targeted graveyard exile outcome descriptor fields are closed')
        if type(value['scope']) is not str or type(value['outcome']) is not str:
            raise ValueError('Targeted graveyard exile scope and outcome must be strings')
        return cls(GraveyardExileTargetScope(value['scope']), CreatureExileOutcome(value['outcome']), value['schema_version'])
