from __future__ import annotations

"""Closed resolved all-able-blocker rule, distinct from an object ability."""

from dataclasses import dataclass
from typing import Mapping

from .declaration_fragments import DeclarationRequirementTemplate


TEMPORARY_ALL_BLOCKER_KIND = 'all_able_blockers'
TEMPORARY_ALL_BLOCKER_CAPABILITY = 'combat.requirement.resolved_all_blockers'


@dataclass(frozen=True, slots=True)
class TemporaryAllBlockerSpec:
    schema_version: int = 1
    kind: str = TEMPORARY_ALL_BLOCKER_KIND

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1 or self.kind != TEMPORARY_ALL_BLOCKER_KIND:
            raise ValueError('Temporary all-blocker requirements have one closed version and kind')

    def to_dict(self):
        return {'schema_version': self.schema_version, 'kind': self.kind}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'kind'}:
            raise ValueError('Temporary all-blocker requirement fields are closed')
        return cls(**value)

    def to_requirement(self):
        return DeclarationRequirementTemplate('intrinsic-all-able-blockers-v1', 'block', 'all_able_blockers')
