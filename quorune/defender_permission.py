from __future__ import annotations

"""Closed CR609.4 permission to disregard only the Defender restriction."""

from dataclasses import dataclass
from typing import Any, Mapping

DEFENDER_PERMISSION_CAPABILITY = 'combat.attack.defender_permission'
DEFENDER_PERMISSION_HANDLER = 'ability.static.defender-permission.v1'
CONDITIONAL_DEFENDER_PERMISSION_HANDLER = 'continuous.defender-permission.public-state.v1'
DEFENDER_TEMPORARY_CAPABILITY = 'combat.attack.defender_permission.temporary'
DEFENDER_TEMPORARY_MECHANIC = 'fixed-temporary-defender-permission'
DEFENDER_TEMPORARY_OPERATION = 'apply_source_characteristics_until_end_of_turn'


@dataclass(frozen=True, slots=True)
class DefenderAttackPermission:
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported Defender attack permission schema')

    def to_dict(self) -> dict[str, int]:
        return {'schema_version': self.schema_version}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> 'DefenderAttackPermission':
        if not isinstance(value, Mapping) or set(value) != {'schema_version'}:
            raise ValueError('Defender attack permission has a closed schema')
        return cls(**dict(value))
