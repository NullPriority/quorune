from __future__ import annotations

"""Closed source-owned replacement for fixed external entry-tapped subjects."""

from dataclasses import dataclass
from typing import Any,Mapping


EXTERNAL_ENTRY_STATE_HANDLER_ID='replacement.zone.external-entry-tapped.v1'
EXTERNAL_ENTRY_STATE_CAPABILITY='zone.entry.tapped_state.external_source'
PERMANENT_CARD_TYPES=frozenset({'artifact','battle','creature','enchantment','land','planeswalker'})


@dataclass(frozen=True,slots=True)
class ExternalEntryStateSpec:
    source_relation: str
    types_any: tuple[str,...]=()

    def __post_init__(self):
        if type(self.source_relation) is not str or self.source_relation not in {'all','controller','opponents'}:
            raise ValueError('External entry state requires a fixed controller relation')
        if (not isinstance(self.types_any,tuple)
                or any(type(value) is not str or value not in PERMANENT_CARD_TYPES for value in self.types_any)
                or tuple(sorted(set(self.types_any)))!=self.types_any):
            raise ValueError('External entry state requires canonical fixed permanent types')

    def to_dict(self)->dict[str,Any]:
        return {'source_relation':self.source_relation,'types_any':list(self.types_any)}

    @classmethod
    def from_dict(cls,value:Mapping[str,Any])->ExternalEntryStateSpec:
        if not isinstance(value,Mapping) or set(value)!={'source_relation','types_any'} or not isinstance(value['types_any'],list):
            raise ValueError('External entry state has a closed schema')
        return cls(value['source_relation'],tuple(value['types_any']))
