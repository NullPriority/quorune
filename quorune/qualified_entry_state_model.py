from __future__ import annotations

"""Closed qualified external entry-state descriptors and source snapshots."""

from dataclasses import dataclass
from typing import Any, Mapping

from .entry_type_scopes import entry_type_scopes
from .object_predicate import ObjectQuerySpec


QUALIFIED_ENTRY_STATE_HANDLER = 'replacement.zone.external-entry-state.v2'
QUALIFIED_ENTRY_STATE_CAPABILITY = 'zone.entry.tapped_state.qualified_external_source'


@dataclass(frozen=True, slots=True)
class QualifiedEntryStateSpec:
    source_relation: str
    subject_queries: tuple[ObjectQuerySpec, ...]
    exclude_source: bool = False
    source_tapped: bool | None = None
    tapped: bool = True

    def __post_init__(self):
        if type(self.source_relation) is not str or self.source_relation not in {'all', 'controller', 'opponents'}:
            raise ValueError('Qualified entry state requires a fixed controller relation')
        if type(self.exclude_source) is not bool or type(self.tapped) is not bool:
            raise ValueError('Qualified entry state flags must be booleans')
        if self.source_tapped is not None and type(self.source_tapped) is not bool:
            raise ValueError('Qualified entry source state must be boolean or null')
        if not entry_type_scopes(self.subject_queries):
            raise ValueError('Qualified entry state requires a nonempty subject')

    def to_dict(self) -> dict[str, Any]:
        return {'source_relation': self.source_relation, 'subject_queries': [query.to_dict() for query in self.subject_queries],
                'exclude_source': self.exclude_source, 'source_tapped': self.source_tapped, 'tapped': self.tapped}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> QualifiedEntryStateSpec:
        if not isinstance(value, Mapping) or set(value) != {
                'source_relation', 'subject_queries', 'exclude_source', 'source_tapped', 'tapped'}:
            raise ValueError('Qualified entry state has a closed schema')
        if not isinstance(value['subject_queries'], list):
            raise ValueError('Qualified entry subjects must be an array')
        return cls(**{**value, 'subject_queries': tuple(ObjectQuerySpec.from_dict(query)
                   for query in value['subject_queries'])})


@dataclass(frozen=True, slots=True)
class EntryStateSourceSnapshot:
    source_ref: str
    controller: str
    tapped: bool

    def __post_init__(self):
        if any(type(value) is not str or not value for value in (self.source_ref, self.controller)):
            raise ValueError('Entry state source snapshot requires current identities')
        if type(self.tapped) is not bool:
            raise ValueError('Entry state source snapshot requires boolean tap state')
