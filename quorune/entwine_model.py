from __future__ import annotations

"""Immutable additional mana price for choosing all printed modes."""

from dataclasses import dataclass
from typing import Mapping

from .replacement.immutable import FrozenMap,thaw_value
from .spree_model import MANA_KEYS

ENTWINE_FIXED_MECHANIC='fixed-entwine-mode-costs'
ENTWINE_FIXED_CAPABILITY='casting.additional_cost.entwine_fixed_modes'


@dataclass(frozen=True,slots=True)
class EntwineModeCost:
    mode_ids: tuple[str,...]
    mana: FrozenMap
    schema_version: int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1:
            raise ValueError('Unsupported Entwine cost version')
        if not isinstance(self.mode_ids,(list,tuple)) or len(self.mode_ids) not in {2,3,4,5}:
            raise ValueError('Entwine requires a bounded printed mode list')
        expected=tuple('mode_'+str(index) for index in range(1,len(self.mode_ids)+1))
        if tuple(self.mode_ids)!=expected:raise ValueError('Entwine modes must retain printed order')
        if (not isinstance(self.mana,Mapping) or set(self.mana)!=set(MANA_KEYS)
                or any(type(value) is not int or value<0 for value in self.mana.values())):
            raise ValueError('Entwine requires one canonical fixed mana vector')
        object.__setattr__(self,'mode_ids',expected)
        object.__setattr__(self,'mana',FrozenMap(self.mana))

    def to_dict(self):
        return dict(schema_version=self.schema_version,mode_ids=list(self.mode_ids),mana=thaw_value(self.mana))

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','mode_ids','mana'}:
            raise ValueError('Entwine price fields are closed')
        return cls(value['mode_ids'],value['mana'],value['schema_version'])

    def validate_schema(self,schema):
        if (not isinstance(schema,Mapping) or set(schema)!={'mode_count','min_modes','max_modes','modes'}
                or any(type(schema[key]) is not int for key in ('mode_count','min_modes','max_modes'))
                or schema['mode_count']!=1 or schema['min_modes']!=1 or schema['max_modes']!=len(self.mode_ids)
                or not isinstance(schema['modes'],Mapping) or tuple(schema['modes'])!=self.mode_ids):
            raise ValueError('Entwine price must match the complete nonrepeating modal schema')

    def selections(self):
        return tuple((mode,) for mode in self.mode_ids)+(self.mode_ids,)

    def additional_mana(self,modes):
        selected=tuple(modes)
        if len(selected)==1 and selected[0] in self.mode_ids:return {key:0 for key in MANA_KEYS}
        if selected==self.mode_ids:return dict(self.mana)
        raise ValueError('Entwine selects one mode or every printed mode')
