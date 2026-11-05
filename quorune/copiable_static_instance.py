from __future__ import annotations

"""Creation time for one copied instance of a compiled static component."""

from collections.abc import Mapping
from dataclasses import dataclass

from .ability_fragment_primitives import AbilityFragmentError


@dataclass(frozen=True,slots=True)
class CopiableStaticInstanceSpec:
    semantic_key: str
    creation_timestamp: int
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or type(self.semantic_key) is not str or not self.semantic_key.strip()
                or self.semantic_key!=self.semantic_key.strip()
                or type(self.creation_timestamp) is not int or self.creation_timestamp<0):
            raise AbilityFragmentError('Copied static instances require their closed key and creation timestamp')

    @property
    def capability_id(self):
        return 'continuous.characteristics.composition_copiable_values'

    def to_dict(self):
        return {'schema_version':self.schema_version,'semantic_key':self.semantic_key,
            'creation_timestamp':self.creation_timestamp}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','semantic_key','creation_timestamp'}:
            raise AbilityFragmentError('Copied static instances have a closed wire shape')
        return cls(**dict(value))
