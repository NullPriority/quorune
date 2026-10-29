"""Typed Champion subjects and original-source exclusion under CR702.72."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping

from .continuous_effect_model import ContinuousObjectIdentity
from .creature_subtypes import canonical_creature_subtype
from .object_predicate import ObjectQuerySpec
from .object_query import object_matches_query


CHAMPION_MECHANIC = 'champion'
CHAMPION_CAPABILITY = 'keyword.champion.linked_exile'
CHAMPION_CHOICE_OPERATION = 'choose_champion_exile'


@dataclass(frozen=True, slots=True)
class ChampionSpec:
    predicate: ObjectQuerySpec
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported Champion schema')
        if not isinstance(self.predicate, ObjectQuerySpec) or self.predicate.zones != ('battlefield',):
            raise ValueError('Champion requires a public permanent predicate')
        expected = (ObjectQuerySpec(zones=('battlefield',), types_all=('creature',))
                    if self.predicate.types_all == ('creature',) else
                    ObjectQuerySpec(zones=('battlefield',), subtypes_any=self.predicate.subtypes_any))
        if self.predicate != expected or not self.predicate.types_all and not self.predicate.subtypes_any:
            raise ValueError('Champion predicate has unrepresented qualifiers')
        if self.predicate.subtypes_any and (len(self.predicate.subtypes_any) > 2 or any(
            canonical_creature_subtype(value) != value for value in self.predicate.subtypes_any
        )):
            raise ValueError('Champion subtype alternatives must be represented creature types')

    def to_dict(self):
        return {'schema_version': 1, 'predicate': self.predicate.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'predicate'}:
            raise ValueError('Champion specification has a closed schema')
        return cls(ObjectQuerySpec.from_dict(value['predicate']), value['schema_version'])


def champion_candidates(rows, *, spec, actor, source):
    if not isinstance(spec, ChampionSpec) or not isinstance(source, ContinuousObjectIdentity):
        raise ValueError('Champion choices require typed source and subject facts')
    if type(actor) is not str or not actor:
        raise ValueError('Champion choices require a locked controller')
    values = []
    for row in rows:
        identity = ContinuousObjectIdentity(row.object_id, row.logical_object_id)
        if (row.controller == actor and not row.phased_out and identity != source
                and object_matches_query(row, spec.predicate)):
            values.append(row)
    if len({row.ref for row in values}) != len(values):
        raise ValueError('Champion choice references must be unique')
    return tuple(sorted(values, key=lambda row: (row.ref, row.logical_object_id)))
