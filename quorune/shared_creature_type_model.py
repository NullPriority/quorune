"""Immutable current creature-type membership for a recipient-relative count."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .continuous_effect_model import ContinuousObjectIdentity
from .creature_subtypes import canonical_creature_subtype


SHARED_CREATURE_TYPE_QUERY_CAPABILITY = 'state_query.permanent.shared_creature_type'


def canonical_creature_types(values):
    if (not isinstance(values, (list, tuple, set, frozenset))
            or any(type(value) is not str for value in values)):
        raise ValueError('Creature-type values must be an explicit collection of strings')
    return tuple(sorted({kind for value in values
                         if (kind := canonical_creature_subtype(value)) is not None}))


@dataclass(frozen=True, slots=True)
class SharedCreatureTypeObject:
    identity: ContinuousObjectIdentity
    creature_types: tuple[str, ...]
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported shared creature-type object version')
        if not isinstance(self.identity, ContinuousObjectIdentity):
            raise ValueError('A shared-type member requires its exact incarnation')
        if (not isinstance(self.creature_types, tuple)
                or self.creature_types != canonical_creature_types(self.creature_types)):
            raise ValueError('Shared creature types must be a canonical distinct tuple')

    def to_dict(self):
        return {'schema_version': 1, 'identity': self.identity.to_dict(),
                'creature_types': list(self.creature_types)}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'schema_version','identity','creature_types'}
                or not isinstance(value['creature_types'], (list, tuple))):
            raise ValueError('Shared creature-type object fields are malformed')
        return cls(ContinuousObjectIdentity.from_dict(value['identity']),
                   tuple(value['creature_types']), value['schema_version'])


def shared_type_objects(values):
    if not isinstance(values, (list, tuple)):
        raise ValueError('Shared-type membership must be an explicit array')
    objects = tuple(value if isinstance(value, SharedCreatureTypeObject)
                    else SharedCreatureTypeObject.from_dict(value) for value in values)
    if len({value.identity.object_id for value in objects}) != len(objects):
        raise ValueError('A shared-type snapshot cannot repeat a physical object')
    if len({value.identity.logical_object_id for value in objects}) != len(objects):
        raise ValueError('A shared-type snapshot cannot repeat a logical incarnation')
    return tuple(sorted(objects, key=lambda value: value.identity))


def shared_creature_type_count(objects, *, recipient, creature_types):
    if not isinstance(recipient, ContinuousObjectIdentity):
        raise ValueError('A shared-type quantity requires its recipient incarnation')
    types = frozenset(canonical_creature_types(creature_types))
    return sum(value.identity != recipient and bool(types.intersection(value.creature_types))
               for value in shared_type_objects(objects))
