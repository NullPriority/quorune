from __future__ import annotations

"""One fixed instant/sorcery stack-copy instruction with optional new targets."""

from collections.abc import Mapping
from dataclasses import dataclass


STACK_SPELL_COPY_CAPABILITY = 'stack.copy.fixed_instant_or_sorcery'
STACK_SPELL_COPY_MECHANIC = 'fixed-stack-spell-copy'


@dataclass(frozen=True, slots=True)
class FixedStackSpellCopySpec:
    controlled_only: bool = False
    schema_version: int = 1

    def __post_init__(self):
        if type(self.controlled_only) is not bool or type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Fixed spell copy requires a closed current-controller scope')

    def to_dict(self):
        return {'schema_version': self.schema_version, 'controlled_only': self.controlled_only}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'controlled_only'}:
            raise ValueError('Fixed spell-copy declaration fields are closed')
        return cls(**dict(value))

    @property
    def effects(self):
        return ({'op': 'copy_stack_item', 'stack': '$target.0'},)

    @property
    def target_schema(self):
        return {'zones': ['stack'], 'categories': ['spell'], 'source_exclusion': True, 'count': 1,
            'types_any': ['instant', 'sorcery'], **({'controller_relation': 'you'} if self.controlled_only else {})}

    @property
    def mechanics(self):
        return (STACK_SPELL_COPY_MECHANIC, 'cr-115-targets')
