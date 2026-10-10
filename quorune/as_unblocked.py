from __future__ import annotations

"""Typed CR609.4 combat assignment permission with unchanged blocked status."""

from dataclasses import dataclass
from typing import Any, Mapping

AS_UNBLOCKED_CAPABILITY = 'combat.damage.assignment.as_unblocked'
AS_UNBLOCKED_HANDLER = 'ability.static.as-unblocked-assignment.v1'
CONDITIONAL_AS_UNBLOCKED_HANDLER = 'continuous.as-unblocked-assignment.public-state.v1'
TEMPORARY_AS_UNBLOCKED_MECHANIC = 'temporary-as-unblocked-assignment'


@dataclass(frozen=True, slots=True)
class AsUnblockedAssignmentPermission:
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported as-unblocked assignment permission version')

    def to_dict(self):
        return {'schema_version': self.schema_version}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]):
        if not isinstance(value, Mapping) or set(value) != {'schema_version'}:
            raise ValueError('As-unblocked assignment permissions have a closed schema')
        return cls(**dict(value))


def can_assign_as_unblocked(fragments):
    return any(isinstance(fragment,AsUnblockedAssignmentPermission) for fragment in fragments)
