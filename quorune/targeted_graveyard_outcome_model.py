"""Closed target scopes and outcomes for committed graveyard card exile."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class GraveyardExileTargetScope(StrEnum):
    ONE = 'one'
    UP_TO_ONE = 'up_to_one'
    UP_TO_TWO = 'up_to_two'
    UP_TO_TWO_SAME_GRAVEYARD = 'up_to_two_same_graveyard'
    UP_TO_DEALT_DAMAGE = 'up_to_dealt_damage'

    @property
    def minimum(self) -> int:
        return 1 if self is self.ONE else 0

    @property
    def maximum(self) -> int:
        if self is self.UP_TO_DEALT_DAMAGE:
            raise ValueError('Combat damage sets this target bound at trigger placement')
        return 2 if self in (self.UP_TO_TWO, self.UP_TO_TWO_SAME_GRAVEYARD) else 1

    @property
    def same_graveyard(self) -> bool:
        return self is self.UP_TO_TWO_SAME_GRAVEYARD


class CreatureExileOutcome(StrEnum):
    DRAIN_TWO_ONCE = 'drain_two_once'
    GAIN_ONE_PER_CREATURE = 'gain_one_per_creature'
    CREATE_ONE_CLUE = 'create_one_clue'
    CREATE_ONE_FOOD = 'create_one_food'
    COUNTERS_AND_LIFE_FROM_PARTITION = 'counters_and_life_from_partition'


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
        if type(self.schema_version) is not int:
            raise ValueError('Targeted graveyard exile outcome requires an integer schema version')
        if not isinstance(self.scope, GraveyardExileTargetScope) or not isinstance(self.outcome, CreatureExileOutcome):
            raise ValueError('Targeted graveyard exile requires a typed scope and outcome')
        if self.damage_linked:
            if self.schema_version != 2 or self.outcome is not CreatureExileOutcome.COUNTERS_AND_LIFE_FROM_PARTITION:
                raise ValueError('Damage-linked exile requires its exact version-two outcome')
            return
        if self.schema_version != 1 or self.outcome not in _OUTCOMES_BY_SCOPE[self.scope]:
            raise ValueError('Targeted graveyard exile scope and outcome are not supported together')

    @property
    def damage_linked(self) -> bool:
        return self.scope is GraveyardExileTargetScope.UP_TO_DEALT_DAMAGE

    def to_dict(self) -> dict[str, str | int]:
        return {'schema_version': self.schema_version, 'scope': self.scope.value, 'outcome': self.outcome.value}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> 'TargetedGraveyardExileOutcomeSpec':
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'scope', 'outcome'}:
            raise ValueError('Targeted graveyard exile outcome descriptor fields are closed')
        if type(value['scope']) is not str or type(value['outcome']) is not str:
            raise ValueError('Targeted graveyard exile scope and outcome must be strings')
        return cls(GraveyardExileTargetScope(value['scope']), CreatureExileOutcome(value['outcome']), value['schema_version'])
