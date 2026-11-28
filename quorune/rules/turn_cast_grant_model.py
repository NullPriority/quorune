from __future__ import annotations

"""Closed source-granted casting permission and its current-turn usage binding."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from ..object_predicate import ObjectQuerySpec

TURN_CAST_GRANT_CAPABILITY = 'casting.permission.source_once_per_turn'
TURN_CAST_GRANT_HANDLER = 'permission.cast.source-once-own-turn.v1'
TURN_CAST_GRANT_EVENT = 'cast.cost'
TURN_CAST_GRANT_FIELD = 'source_cast_grant'
TURN_CAST_EXILE_CAPABILITY = 'casting.permission.source_graveyard_exile'
TURN_CAST_TAPPED_CAPABILITY = 'casting.permission.source_tapped_entry'
TURN_CAST_EXILE_HANDLER = 'permission.cast.source-once-own-turn.graveyard-exile.v1'
TURN_CAST_TAPPED_HANDLER = 'permission.cast.source-once-own-turn.tapped-entry.v1'
TURN_CAST_MILL_CAPABILITY = 'casting.permission.source_current_mill'
TURN_CAST_LIBRARY_SHARED_CAPABILITY = 'casting.permission.source_library_arrival_shared_turn'
TURN_CAST_MILL_HANDLER = 'permission.cast.source-current-mill.v1'
TURN_CAST_LIBRARY_SHARED_HANDLER = 'permission.action.source-library-arrival-shared.v1'
TURN_CAST_NONBATTLEFIELD_CAPABILITY = 'casting.permission.source_nonbattlefield_arrival'
TURN_CAST_NONBATTLEFIELD_HANDLER = 'permission.cast.source-nonbattlefield-arrival.v1'
COMMAND_GRAVEYARD_CAST_CAPABILITY = 'casting.permission.command_owned_graveyard'
COMMAND_GRAVEYARD_CAST_HANDLER = 'permission.cast.command-graveyard-exile.v1'
TURN_CAST_PERMANENT_TYPE_CAPABILITY = 'casting.permission.source_graveyard_permanent_types'
TURN_CAST_PERMANENT_TYPE_HANDLER = 'permission.action.source-graveyard-permanent-types.v1'
GRAVEYARD_SPELL_PERMANENT_TYPES = ('artifact', 'battle', 'creature', 'enchantment', 'planeswalker')
TURN_CAST_GRANT_HANDLERS = frozenset({TURN_CAST_GRANT_HANDLER, TURN_CAST_EXILE_HANDLER, TURN_CAST_TAPPED_HANDLER,
    TURN_CAST_MILL_HANDLER, TURN_CAST_LIBRARY_SHARED_HANDLER, TURN_CAST_NONBATTLEFIELD_HANDLER,
    COMMAND_GRAVEYARD_CAST_HANDLER, TURN_CAST_PERMANENT_TYPE_HANDLER})
TURN_CAST_RIDER_HANDLERS = {'none': TURN_CAST_GRANT_HANDLER,
    'graveyard_exile': TURN_CAST_EXILE_HANDLER, 'tapped_entry': TURN_CAST_TAPPED_HANDLER}
TURN_CAST_RIDER_CAPABILITIES = {'graveyard_exile': TURN_CAST_EXILE_CAPABILITY, 'tapped_entry': TURN_CAST_TAPPED_CAPABILITY}


class TurnCastGrantScope(StrEnum):
    ONCE_ON_OWN_TURN = "once_on_own_turn"
    COMMAND_UNLIMITED = "command_unlimited"
    PER_PERMANENT_TYPE_ON_OWN_TURN = "per_permanent_type_on_own_turn"


@dataclass(frozen=True, slots=True)
class TurnCastGrantSpec:
    origins: tuple[str, ...]
    predicates_any: tuple[ObjectQuerySpec, ...]
    without_mana_cost: bool = False
    cast_rider: str = 'none'
    qualification: str = 'none'
    allow_land_play: bool = False
    source_scope: TurnCastGrantScope = TurnCastGrantScope.ONCE_ON_OWN_TURN

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
        if self.qualification not in {'none','milled_this_turn','library_arrival_this_turn','nonbattlefield_arrival_this_turn'} or type(self.allow_land_play) is not bool:
            raise ValueError('Turn casting grant qualification is unsupported')
        if self.qualification != 'none' and (self.origins != ('graveyard',) or self.without_mana_cost or self.cast_rider != 'none'):
            raise ValueError('Current-arrival grants require one ordinary graveyard route')
        if self.allow_land_play != (self.qualification == 'library_arrival_this_turn' or self.per_permanent_type):
            raise ValueError('Land permission requires its complete qualified or per-type scope')
        if self.per_permanent_type and (
            self.origins != ("graveyard",)
            or self.predicates_any != (ObjectQuerySpec(types_any=GRAVEYARD_SPELL_PERMANENT_TYPES),)
            or self.qualification != "none" or self.without_mana_cost or self.cast_rider != "none"
        ):
            raise ValueError("Per-type permissions require the unrestricted owned graveyard permanent scope")
        if not isinstance(self.source_scope, TurnCastGrantScope):
            raise ValueError("Casting grants require a typed source scope")
        if self.source_scope is TurnCastGrantScope.COMMAND_UNLIMITED and (
            self.origins != ("graveyard",)
            or self.predicates_any != (ObjectQuerySpec(types_any=("instant", "sorcery")),)
            or self.without_mana_cost or self.cast_rider != "graveyard_exile"
            or self.qualification != "none" or self.allow_land_play
        ):
            raise ValueError("A command casting grant requires the exact owned graveyard spell and exile scope")

    @property
    def per_permanent_type(self):
        return self.source_scope is TurnCastGrantScope.PER_PERMANENT_TYPE_ON_OWN_TURN

    @property
    def handler_id(self):
        if self.per_permanent_type:
            return TURN_CAST_PERMANENT_TYPE_HANDLER
        if self.source_scope is TurnCastGrantScope.COMMAND_UNLIMITED:
            return COMMAND_GRAVEYARD_CAST_HANDLER
        if self.qualification == 'nonbattlefield_arrival_this_turn':return TURN_CAST_NONBATTLEFIELD_HANDLER
        if self.qualification == 'milled_this_turn':return TURN_CAST_MILL_HANDLER
        if self.qualification == 'library_arrival_this_turn':return TURN_CAST_LIBRARY_SHARED_HANDLER
        return TURN_CAST_RIDER_HANDLERS[self.cast_rider]

    @property
    def capability_dependencies(self):
        if self.per_permanent_type:
            return TURN_CAST_GRANT_CAPABILITY, TURN_CAST_PERMANENT_TYPE_CAPABILITY
        if self.source_scope is TurnCastGrantScope.COMMAND_UNLIMITED:
            return (COMMAND_GRAVEYARD_CAST_CAPABILITY, TURN_CAST_EXILE_CAPABILITY)
        if self.qualification != 'none':
            if self.qualification=='nonbattlefield_arrival_this_turn':return (TURN_CAST_GRANT_CAPABILITY,TURN_CAST_NONBATTLEFIELD_CAPABILITY)
            return (TURN_CAST_GRANT_CAPABILITY,TURN_CAST_MILL_CAPABILITY if self.qualification=='milled_this_turn' else TURN_CAST_LIBRARY_SHARED_CAPABILITY)
        return (TURN_CAST_GRANT_CAPABILITY,) if self.cast_rider == 'none' else (
            TURN_CAST_GRANT_CAPABILITY, TURN_CAST_RIDER_CAPABILITIES[self.cast_rider])

    def to_dict(self):
        if self.per_permanent_type:
            version = 5
        elif self.source_scope is TurnCastGrantScope.COMMAND_UNLIMITED:
            version = 4
        elif self.qualification != 'none':
            version = 3
        else:
            version = 1 if self.cast_rider == 'none' else 2
        return {
            'schema_version': version,
            'origins': list(self.origins),
            'predicates_any': [p.to_dict() for p in self.predicates_any],
            'without_mana_cost': self.without_mana_cost,
            **({'cast_rider': self.cast_rider} if self.cast_rider != 'none' else {}),
            **({'qualification': self.qualification, 'allow_land_play': self.allow_land_play} if version == 3 else {}),
            **({'source_scope': self.source_scope.value} if version in (4, 5) else {}),
            **({'allow_land_play': self.allow_land_play} if version == 5 else {}),
        }

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping):
            raise ValueError('Turn casting grant must be an object')
        version = value.get('schema_version')
        fields = {'schema_version', 'origins', 'predicates_any', 'without_mana_cost'}
        extended_fields = {
            1: set(),
            2: {'cast_rider'},
            3: {'qualification', 'allow_land_play'},
            4: {'cast_rider', 'source_scope'},
            5: {'source_scope', 'allow_land_play'},
        }
        if (
            type(version) is not int or version not in extended_fields
            or set(value) != fields | extended_fields[version]
            or not isinstance(value['origins'], (list, tuple))
            or not isinstance(value['predicates_any'], (list, tuple))
        ):
            raise ValueError('Turn casting grant descriptor fields are closed')
        spec = cls(
            origins=tuple(value['origins']),
            predicates_any=tuple(ObjectQuerySpec.from_dict(p) for p in value['predicates_any']),
            without_mana_cost=value['without_mana_cost'],
            cast_rider=value.get('cast_rider', 'none'),
            qualification=value.get('qualification', 'none'),
            allow_land_play=value.get('allow_land_play', False),
            source_scope=TurnCastGrantScope(value.get('source_scope', TurnCastGrantScope.ONCE_ON_OWN_TURN)),
        )
        if spec.to_dict()['schema_version'] != version:
            raise ValueError('Turn casting grant version does not match its result')
        return spec


def permanent_type_graveyard_grant():
    return TurnCastGrantSpec(
        ("graveyard",), (ObjectQuerySpec(types_any=GRAVEYARD_SPELL_PERMANENT_TYPES),),
        allow_land_play=True, source_scope=TurnCastGrantScope.PER_PERMANENT_TYPE_ON_OWN_TURN,
    )


def cast_grant_usage_key(program, chosen_type=None):
    return program if chosen_type is None else f"{program}:permanent-type:{chosen_type}"


def validate_turn_cast_grant_use(value):
    fields = {'source', 'logical', 'program', 'source_ref', 'source_label', 'turn', 'grant', 'original_cost_id'}
    if (not isinstance(value, Mapping) or set(value) not in (fields, fields | {"permanent_type"})
            or any(type(value[k]) is not str or not value[k] for k in ('source', 'logical', 'program', 'source_ref', 'source_label', 'original_cost_id'))
            or type(value['turn']) is not int or value['turn'] < 0):
        raise ValueError('Turn casting grant use has a closed source/turn identity')
    spec = TurnCastGrantSpec.from_dict(value['grant'])
    chosen = value.get("permanent_type")
    if (spec.per_permanent_type != ("permanent_type" in value)
            or spec.per_permanent_type and chosen not in (*GRAVEYARD_SPELL_PERMANENT_TYPES, "land")):
        raise ValueError("A per-type graveyard action requires an exact chosen permanent type")
    return spec
