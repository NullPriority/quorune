from __future__ import annotations

"""Closed current player-attachment characteristic and combat rule identities."""

from dataclasses import dataclass
from typing import Mapping

ENCHANTED_PLAYER_CHARACTERISTICS_CAPABILITY='continuous.characteristics.enchanted_player_query'
ENCHANTED_PLAYER_CHARACTERISTICS_HANDLER='continuous.characteristics.enchanted-player.v1'
ENCHANTED_PLAYER_ATTACK_CAPABILITY='combat.requirement.enchanted_player'
ENCHANTED_PLAYER_ATTACK_HANDLER='ability.static.enchanted-player-attack.v1'


@dataclass(frozen=True,slots=True)
class EnchantedPlayerAttackRequirementSpec:
    schema_version:int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1:
            raise ValueError('Enchanted-player attack requirements have one closed version')

    @property
    def capability_id(self):return ENCHANTED_PLAYER_ATTACK_CAPABILITY

    def to_dict(self):return {'schema_version':1}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version'}:
            raise ValueError('Enchanted-player attack rule fields are closed')
        return cls(value['schema_version'])
