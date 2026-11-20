"""Closed destination roles for fixed tokens created for each opponent."""
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum


class OpponentAttackDestination(str, Enum):
    PLAYER = 'opponent_player'
    PLAYER_OR_PLANESWALKER = 'opponent_player_or_planeswalker'


@dataclass(frozen=True, slots=True)
class AttackingOpponentScope:
    destination: OpponentAttackDestination
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or not isinstance(self.destination, OpponentAttackDestination)):
            raise ValueError('Opponent attack destinations require an explicit closed scope')

    @property
    def permitted_destination_kinds(self):
        return ('player', 'planeswalker') if self.destination is OpponentAttackDestination.PLAYER_OR_PLANESWALKER else ('player',)

    def to_dict(self):
        return {'schema_version': self.schema_version, 'destination': self.destination.value}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'destination'}:
            raise ValueError('Opponent attack destination fields are closed')
        try:
            destination = OpponentAttackDestination(value['destination'])
        except (TypeError, ValueError) as exc:
            raise ValueError('Opponent attack destination is unsupported') from exc
        return cls(destination, value['schema_version'])
