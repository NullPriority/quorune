from __future__ import annotations

"""Closed compiled-program provenance for one copiable ability instance set."""

from collections.abc import Mapping
from dataclasses import dataclass

from .ability_fragment_primitives import AbilityFragmentError


@dataclass(frozen=True,slots=True)
class CopiableProgramSourceSpec:
    oracle_id: str
    face_id: str
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or any(type(value) is not str or not value.strip() or value!=value.strip()
                    for value in (self.oracle_id,self.face_id))):
            raise AbilityFragmentError('Copiable program sources require their closed identity and face')

    def to_dict(self):
        return {'schema_version':self.schema_version,'oracle_id':self.oracle_id,'face_id':self.face_id}

    @property
    def capability_id(self):
        return 'continuous.characteristics.composition_copiable_values'

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','oracle_id','face_id'}:
            raise AbilityFragmentError('Copiable program sources have a closed wire shape')
        return cls(**dict(value))
