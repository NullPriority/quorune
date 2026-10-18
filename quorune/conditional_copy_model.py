from __future__ import annotations

"""Typed noncopiable creation origin for conditional copy exceptions."""

from dataclasses import dataclass
from collections.abc import Mapping

EMBALMED_DESIGNATION='embalmed_creation_v1'
CONDITIONAL_COPY_HANDLER='replacement.zone.entry-copy.v5'
CONDITIONAL_COPY_CAPABILITY='zone.entry.conditional_copy_exception'


@dataclass(frozen=True,slots=True)
class ConditionalCopyException:
    condition: str='was_embalmed'
    schema_version: int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1 or self.condition!='was_embalmed':raise ValueError('Conditional copy exception is closed')

    def to_dict(self):return {'schema_version':1,'condition':self.condition}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','condition'}:raise ValueError('Conditional copy fields are closed')
        return cls(value['condition'],value['schema_version'])
