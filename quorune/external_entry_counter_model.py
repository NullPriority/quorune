from __future__ import annotations

"""Immutable source semantics for an external entry-counter replacement."""

from dataclasses import dataclass
from typing import Mapping

EXTERNAL_ENTRY_COUNTER_CAPABILITY='counter.producer.external_additional_entry'
EXTERNAL_ENTRY_COUNTER_HANDLER='replacement.zone.external-entry-counter.v1'


@dataclass(frozen=True,slots=True)
class ExternalEntryCounterSpec:
    amount_source: str='fixed_one'
    required_subtype: str | None=None
    excluded_subtype: str | None=None
    add_mutant: bool=False
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or type(self.amount_source) is not str or self.amount_source not in {'fixed_one','source_power','source_toughness','source_plus_one_counters'}
                or type(self.add_mutant) is not bool):
            raise ValueError('External entry-counter amount or version is unsupported')
        for subtype in (self.required_subtype,self.excluded_subtype):
            if subtype is not None:
                from .creature_subtypes import canonical_creature_subtype
                if type(subtype) is not str or canonical_creature_subtype(subtype)!=subtype:
                    raise ValueError('External entry counters require canonical creature subtypes')
        if self.required_subtype is not None and self.excluded_subtype is not None:
            raise ValueError('External entry-counter subtype relations cannot be combined')
        if (self.required_subtype is not None or self.excluded_subtype is not None) and self.amount_source!='fixed_one':
            raise ValueError('Qualified external counter amounts require the fixed-one owner')
        if self.add_mutant and (self.amount_source!='source_power' or self.required_subtype is not None or self.excluded_subtype is not None):
            raise ValueError('Mutant entry accompanies the unqualified source-power instruction')

    def to_dict(self):
        return {'schema_version':self.schema_version,'amount_source':self.amount_source,'required_subtype':self.required_subtype,
            'excluded_subtype':self.excluded_subtype,'add_mutant':self.add_mutant}

    @classmethod
    def from_dict(cls,value):
        fields={'schema_version','amount_source','required_subtype','excluded_subtype','add_mutant'}
        if not isinstance(value,Mapping) or set(value)!=fields:
            raise ValueError('External entry-counter fields are incomplete or unknown')
        return cls(**value)


@dataclass(frozen=True,slots=True)
class ExternalEntryCounterSourceSnapshot:
    object_id: str
    logical_object_id: str
    controller: str
    amount: int

    def __post_init__(self):
        if any(type(v) is not str or not v for v in (self.object_id,self.logical_object_id,self.controller)) or type(self.amount) is not int:
            raise ValueError('External entry counters require a sealed source identity and integer quantity')

    @property
    def nonnegative_amount(self):return max(0,self.amount)
