from __future__ import annotations

"""Closed giver-owned attachment rules for untap steps or every untap event."""

from dataclasses import dataclass
from typing import Mapping

UNTAP_RESTRAINT_CAPABILITY='untap.permission.attached_static_restraint'
UNTAP_RESTRAINT_HANDLER='ability.static.attached-untap-restraint.v1'


@dataclass(frozen=True,slots=True)
class AttachedUntapRestraintSpec:
    mode:str
    types_all:tuple[str,...]=()
    schema_version:int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or type(self.mode) is not str or self.mode not in {'controller_step','all_untaps'}
                or not isinstance(self.types_all,tuple) or len(self.types_all)>1
                or any(type(value) is not str or value not in {'artifact','battle','creature','enchantment','land','planeswalker'} for value in self.types_all)):
            raise ValueError('Attached untap restraints have one closed version, mode and permanent subject')

    @property
    def capability_id(self):return UNTAP_RESTRAINT_CAPABILITY

    def to_dict(self):return {'schema_version':1,'mode':self.mode,'types_all':list(self.types_all)}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','mode','types_all'} or not isinstance(value['types_all'],list):
            raise ValueError('Attached untap restraint fields are closed')
        return cls(value['mode'],tuple(value['types_all']),value['schema_version'])
