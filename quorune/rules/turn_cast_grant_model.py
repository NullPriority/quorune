from __future__ import annotations

"""Closed source-granted casting permission and its current-turn usage binding."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..object_predicate import ObjectQuerySpec

TURN_CAST_GRANT_CAPABILITY = 'casting.permission.source_once_per_turn'
TURN_CAST_GRANT_HANDLER = 'permission.cast.source-once-own-turn.v1'
TURN_CAST_GRANT_EVENT = 'cast.cost'
TURN_CAST_GRANT_FIELD = 'source_cast_grant'
TURN_CAST_EXILE_CAPABILITY = 'casting.permission.source_graveyard_exile'
TURN_CAST_TAPPED_CAPABILITY = 'casting.permission.source_tapped_entry'
TURN_CAST_EXILE_HANDLER = 'permission.cast.source-once-own-turn.graveyard-exile.v1'
TURN_CAST_TAPPED_HANDLER = 'permission.cast.source-once-own-turn.tapped-entry.v1'
TURN_CAST_GRANT_HANDLERS = frozenset({TURN_CAST_GRANT_HANDLER, TURN_CAST_EXILE_HANDLER, TURN_CAST_TAPPED_HANDLER})
TURN_CAST_RIDER_HANDLERS = {'none': TURN_CAST_GRANT_HANDLER,
    'graveyard_exile': TURN_CAST_EXILE_HANDLER, 'tapped_entry': TURN_CAST_TAPPED_HANDLER}
TURN_CAST_RIDER_CAPABILITIES = {'graveyard_exile': TURN_CAST_EXILE_CAPABILITY, 'tapped_entry': TURN_CAST_TAPPED_CAPABILITY}


@dataclass(frozen=True, slots=True)
class TurnCastGrantSpec:
    origins: tuple[str, ...]
    predicates_any: tuple[ObjectQuerySpec, ...]
    without_mana_cost: bool = False
    cast_rider: str = 'none'

    def __post_init__(self):
        object.__setattr__(self, 'origins', tuple(self.origins))
        object.__setattr__(self, 'predicates_any', tuple(self.predicates_any))
        if (not self.origins or len(set(self.origins)) != len(self.origins)
                or not set(self.origins) <= {'hand', 'graveyard', 'library_top'}
                or type(self.without_mana_cost) is not bool):
            raise ValueError('Turn casting grant origins or base price are unsupported')
        if not self.predicates_any or len(self.predicates_any) > 2:
            raise ValueError('Turn casting grants require one or two public spell predicates')
        for predicate in self.predicates_any:
            if not isinstance(predicate, ObjectQuerySpec) or predicate != ObjectQuerySpec(
                    types_all=predicate.types_all, types_any=predicate.types_any,
                    subtypes_all=predicate.subtypes_all, excluded_types=predicate.excluded_types):
                raise ValueError('Turn casting grant selector is outside the closed spell grammar')
        if self.cast_rider not in TURN_CAST_RIDER_HANDLERS:
            raise ValueError('Turn casting grant result is unsupported')
        if self.cast_rider != 'none' and (self.origins != ('graveyard',) or self.without_mana_cost):
            raise ValueError('Printed casting follow-ups require an ordinary graveyard grant')
        if self.cast_rider == 'tapped_entry' and any('artifact' not in p.types_all for p in self.predicates_any):
            raise ValueError('Artifact entry follow-up requires an artifact spell selector')

    @property
    def capability_dependencies(self):
        return (TURN_CAST_GRANT_CAPABILITY,) if self.cast_rider == 'none' else (
            TURN_CAST_GRANT_CAPABILITY, TURN_CAST_RIDER_CAPABILITIES[self.cast_rider])

    def to_dict(self):
        return {'schema_version': 1 if self.cast_rider == 'none' else 2, 'origins': list(self.origins),
            'predicates_any': [p.to_dict() for p in self.predicates_any], 'without_mana_cost': self.without_mana_cost,
            **({'cast_rider': self.cast_rider} if self.cast_rider != 'none' else {})}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping):
            raise ValueError('Turn casting grant must be an object')
        version = value.get('schema_version')
        fields = {'schema_version', 'origins', 'predicates_any', 'without_mana_cost'}
        if (type(version) is not int or version not in (1, 2)
                or set(value) != fields | ({'cast_rider'} if version == 2 else set())
                or not isinstance(value['origins'], (list, tuple)) or not isinstance(value['predicates_any'], (list, tuple))):
            raise ValueError('Turn casting grant descriptor fields are closed')
        spec = cls(tuple(value['origins']), tuple(ObjectQuerySpec.from_dict(p) for p in value['predicates_any']),
            value['without_mana_cost'], value['cast_rider'] if version == 2 else 'none')
        if spec.to_dict()['schema_version'] != version:
            raise ValueError('Turn casting grant version does not match its result')
        return spec


def validate_turn_cast_grant_use(value):
    if (not isinstance(value, Mapping) or set(value) != {'source', 'logical', 'program', 'source_ref', 'source_label', 'turn', 'grant', 'original_cost_id'}
            or any(type(value[k]) is not str or not value[k] for k in ('source', 'logical', 'program', 'source_ref', 'source_label', 'original_cost_id'))
            or type(value['turn']) is not int or value['turn'] < 0):
        raise ValueError('Turn casting grant use has a closed source/turn identity')
    return TurnCastGrantSpec.from_dict(value['grant'])
