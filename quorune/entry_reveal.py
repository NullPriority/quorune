from __future__ import annotations

"""Closed intrinsic optional hand reveal before battlefield entry."""

from dataclasses import dataclass
from .object_predicate import ObjectQuerySpec
from .creature_subtypes import canonical_creature_subtype

ENTRY_REVEAL_CAPABILITY = 'zone.entry.reveal_hand_or_tapped'
ENTRY_REVEAL_HANDLER = 'replacement.zone.entry-reveal-hand.v1'
ENTRY_REVEAL_FIELD = 'entry_revealed_hand_card'
_LAND_SUBTYPES = frozenset({'forest','island','mountain','plains','swamp'})


@dataclass(frozen=True,slots=True)
class EntryRevealSpec:
    query: ObjectQuerySpec
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1 or not isinstance(self.query,ObjectQuerySpec):
            raise ValueError('Entry reveal requires a typed version-one hand query')
        q=self.query
        if (q.zones != ('hand',) or q.controller is not None or q.owner != '$actor'
                or q.known_to_actor is not True or not q.subtypes_any
                or len(q.subtypes_any)>2 or any(subtype not in _LAND_SUBTYPES and canonical_creature_subtype(subtype)!=subtype for subtype in q.subtypes_any)):
            raise ValueError('Entry reveal supports a closed hand subtype predicate')
        expected=ObjectQuerySpec(zones=('hand',),owner='$actor',known_to_actor=True,subtypes_any=q.subtypes_any)
        if q != expected:raise ValueError('Entry reveal query fields are closed')

    def to_dict(self):
        return {'schema_version':self.schema_version,'query':self.query.to_dict()}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,dict) or set(value)!={'schema_version','query'}:
            raise ValueError('Entry reveal fields are closed')
        return cls(ObjectQuerySpec.from_dict(value['query']),value['schema_version'])
