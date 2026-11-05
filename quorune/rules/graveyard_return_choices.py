"""Closed optional selection from the resolving controller's graveyard."""
from collections.abc import Mapping
from dataclasses import dataclass

from ..object_predicate import ObjectQuerySpec
from ..public_zone_moves import (
    PublicZoneMoveSetSpec, PublicZoneOrigin, PublicZoneDestination,
    PublicZoneRelationAxis, PublicZoneSeatRelation, snapshot_public_zone_move_set,
)
from .graveyard_card_targets import GraveyardCardTargetKind

OPERATION = 'choose_own_graveyard_return'
MECHANIC = 'own-graveyard-return-choice'
CAPABILITY = 'zone.return.own_graveyard_choice'
_PUBLIC_FIELDS = frozenset({'op', 'player', 'selection'})
_CHOICE_FIELDS = frozenset({'_choice_actor', '_legal_refs', '_legal_logical_ids', '_stack_label'})
_TYPES = frozenset({GraveyardCardTargetKind.CREATURE_CARD.types_any,
    GraveyardCardTargetKind.LAND_CARD.types_any, ('creature', 'land'),
    GraveyardCardTargetKind.PERMANENT_CARD.types_any})


@dataclass(frozen=True, slots=True)
class GraveyardReturnSelection:
    types_any: tuple[str, ...]

    def __post_init__(self):
        if (type(self.types_any) is not tuple or any(type(value) is not str for value in self.types_any)
                or self.types_any not in _TYPES):
            raise ValueError('Graveyard return needs one closed positive card-type predicate')

    def to_dict(self):
        return {'schema_version': 1, 'types_any': list(self.types_any)}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'schema_version', 'types_any'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or not isinstance(value['types_any'], (list, tuple))
                or any(type(term) is not str for term in value['types_any'])):
            raise ValueError('Graveyard return selection fields are closed')
        return cls(tuple(value['types_any']))

    @property
    def query(self):
        return ObjectQuerySpec(zones=('graveyard',), types_any=self.types_any)

    def public_set(self):
        return PublicZoneMoveSetSpec(self.query, PublicZoneOrigin.GRAVEYARD,
            PublicZoneDestination.OWNER_HAND, relation_axis=PublicZoneRelationAxis.OWNER,
            seat_relation=PublicZoneSeatRelation.ACTOR, schema_version=2)


def validate_return_choice(effect, *, actor=None, continuation=False):
    fields = _PUBLIC_FIELDS | _CHOICE_FIELDS if continuation else _PUBLIC_FIELDS
    if (not isinstance(effect, Mapping) or set(effect) != fields or effect.get('op') != OPERATION
            or effect.get('player') != (actor if actor is not None else '$controller')):
        raise ValueError('Graveyard return instruction fields or resolving player changed')
    if continuation and effect['_choice_actor'] != actor:
        raise ValueError('Graveyard return chooser changed')
    return GraveyardReturnSelection.from_dict(effect['selection'])


def graveyard_return_candidates(host, actor, effect):
    selection = validate_return_choice(effect, actor=actor, continuation='_choice_actor' in effect)
    snapshot = snapshot_public_zone_move_set(host, actor=actor, spec=selection.public_set())
    return tuple(value.ref for value in snapshot.objects
        if host.state.cards[value.object_id].is_card_object)


def graveyard_return_choice_node_capabilities(*, effects, target_schema, mechanic_ids, **kwargs):
    if MECHANIC not in mechanic_ids or target_schema is not None or len(effects) != 1:
        return ()
    try:
        validate_return_choice(effects[0])
    except (TypeError, ValueError):
        return ()
    return (CAPABILITY,)


def graveyard_return_choice_runtime_dependencies(program):
    def walk(value):
        if isinstance(value, Mapping):
            if value.get('op') == OPERATION:
                required = graveyard_return_choice_node_capabilities(
                    effects=(value,), target_schema=None, mechanic_ids=program.coverage)
                if not required:
                    raise ValueError('Graveyard choice lacks a closed owned instruction')
                return set(required)
            return {cap for child in value.values() for cap in walk(child)}
        if isinstance(value, (tuple, list)):
            return {cap for child in value for cap in walk(child)}
        return set()
    return tuple(sorted(walk((program.effects, program.handlers))))
