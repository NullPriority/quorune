"""Closed selections from the resolving controller's graveyard."""
from collections.abc import Mapping
from dataclasses import dataclass

from ..creature_subtypes import canonical_creature_subtype
from ..object_query import ObjectQuerySpec, object_matches_query
from ..public_zone_moves import (
    PublicZoneMoveSetSpec, PublicZoneOrigin, PublicZoneDestination,
    PublicZoneRelationAxis, PublicZoneSeatRelation, snapshot_public_zone_move_set,
    PublicBattlefieldController, PUBLIC_CHOSEN_GRAVEYARD_RETURN_VERSION,
)
from .graveyard_card_targets import GraveyardCardTargetKind

OPERATION = 'choose_own_graveyard_return'
MECHANIC = 'own-graveyard-return-choice'
CAPABILITY = 'zone.return.own_graveyard_choice'
BOUNDED_BATTLEFIELD_RETURN_CAPABILITY = 'zone.return.own_graveyard_bounded_battlefield_choice'
BOUNDED_BATTLEFIELD_RETURN_VERSION = 4
RETURN_PAIR_FIELD = "graveyard_return_pair"
PAIRED_RETURN_CAPABILITIES = (CAPABILITY, "zone.move.fixed_public_set", "attachment.aura.typed_restriction")


def paired_graveyard_return_effect():
    return {"op": "choose_option", "player": "$controller", RETURN_PAIR_FIELD: {"schema_version": 1}}


def is_paired_graveyard_return(effect):
    return isinstance(effect, Mapping) and effect.get("op") == "choose_option" and RETURN_PAIR_FIELD in effect


def paired_graveyard_return_move(cards):
    spec = PublicZoneMoveSetSpec(
        query=ObjectQuerySpec(zones=("graveyard",), types_all=("creature",), token=False),
        origin=PublicZoneOrigin.GRAVEYARD, destination=PublicZoneDestination.BATTLEFIELD,
        relation_axis=PublicZoneRelationAxis.OWNER, seat_relation=PublicZoneSeatRelation.ANY,
        battlefield_controller=PublicBattlefieldController.OWNER,
        schema_version=PUBLIC_CHOSEN_GRAVEYARD_RETURN_VERSION, chosen_cards=cards,
    )
    return {"op": "move_public_zone_set", "source": "$source", "set": spec.to_dict()}


GREATEST_POWER_CAPABILITY = 'quantity_expression.public_characteristic_aggregate'
QUALIFIED_CHOICE_CAPABILITY = 'zone.return.own_graveyard_subtype_qualified_choice'
_CREATURE_OR_PLANESWALKER = ('creature', 'planeswalker')
MANDATORY_CREATURE_OR_PLANESWALKER_VERSION = 5
_RETAINED_MANDATORY_CREATURE_OR_PLANESWALKER_VERSION = 4
_DISJOINT_RETURN_VERSIONS = frozenset({
    BOUNDED_BATTLEFIELD_RETURN_VERSION,
    _RETAINED_MANDATORY_CREATURE_OR_PLANESWALKER_VERSION,
    MANDATORY_CREATURE_OR_PLANESWALKER_VERSION,
})
_PUBLIC_FIELDS = frozenset({'op', 'player', 'selection'})
_CHOICE_FIELDS = frozenset({'_choice_actor', '_legal_refs', '_legal_logical_ids', '_stack_label'})
_TYPES = frozenset({GraveyardCardTargetKind.CREATURE_CARD.types_any,
    GraveyardCardTargetKind.LAND_CARD.types_any, ('creature', 'land'),
    GraveyardCardTargetKind.PERMANENT_CARD.types_any})


@dataclass(frozen=True, slots=True)
class GraveyardReturnSelection:
    types_any: tuple[str, ...]
    schema_version: int = 1
    excluded_creature_subtype: str | None = None
    mana_value_max: int | None = None

    def __post_init__(self):
        supported_versions = {1, 2, 3} | _DISJOINT_RETURN_VERSIONS
        if type(self.schema_version) is not int or self.schema_version not in supported_versions:
            raise ValueError('Graveyard return requires a supported selection version')
        if type(self.types_any) is not tuple or any(type(value) is not str for value in self.types_any):
            raise ValueError('Graveyard return needs one closed card-type predicate')
        if self.schema_version in _DISJOINT_RETURN_VERSIONS:
            if not (self.mandatory_hand_return or self.returns_to_battlefield):
                raise ValueError('Graveyard return requires an exact mandatory hand or bounded battlefield selection')
            return
        if self.schema_version == 3:
            subtype = self.excluded_creature_subtype
            if (self.types_any != _CREATURE_OR_PLANESWALKER or type(subtype) is not str
                    or canonical_creature_subtype(subtype) != subtype):
                raise ValueError('Qualified return needs an excluded creature subtype and a planeswalker branch')
        elif self.types_any not in _TYPES or self.excluded_creature_subtype is not None:
            raise ValueError('Graveyard return needs one closed positive card-type predicate')
        if self.mana_value_max is not None:
            raise ValueError('Hand return cannot carry a battlefield mana-value maximum')
        if self.greatest_power and self.types_any != ('creature',):
            raise ValueError('Greatest-power return selects only creature cards')

    @property
    def greatest_power(self) -> bool:
        return self.schema_version == 2

    @property
    def returns_to_battlefield(self) -> bool:
        return (
            self.schema_version == BOUNDED_BATTLEFIELD_RETURN_VERSION
            and self.types_any == ('creature',)
            and self.excluded_creature_subtype is None
            and type(self.mana_value_max) is int
            and 0 <= self.mana_value_max <= 20
        )

    @property
    def mandatory_hand_return(self) -> bool:
        return (
            self.schema_version in {
                _RETAINED_MANDATORY_CREATURE_OR_PLANESWALKER_VERSION,
                MANDATORY_CREATURE_OR_PLANESWALKER_VERSION,
            }
            and self.types_any == _CREATURE_OR_PLANESWALKER
            and self.excluded_creature_subtype is None
            and self.mana_value_max is None
        )

    @property
    def required(self) -> bool:
        return self.greatest_power or self.mandatory_hand_return

    def to_dict(self):
        value = {'schema_version': self.schema_version, 'types_any': list(self.types_any)}
        if self.excluded_creature_subtype is not None:
            value['excluded_creature_subtype'] = self.excluded_creature_subtype
        if self.returns_to_battlefield:
            value['mana_value_max'] = self.mana_value_max
        return value

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping):
            raise ValueError('Graveyard return selection fields are closed')
        fields = {'schema_version', 'types_any'}
        if value.get('schema_version') == 3:
            fields.add('excluded_creature_subtype')
        if value.get('schema_version') == BOUNDED_BATTLEFIELD_RETURN_VERSION and 'mana_value_max' in value:
            if type(value['mana_value_max']) is not int:
                raise ValueError('Battlefield return requires an explicit integer mana-value maximum')
            fields.add('mana_value_max')
        if set(value) != fields or not isinstance(value['types_any'], (list, tuple)):
            raise ValueError('Graveyard return selection fields are closed')
        return cls(tuple(value['types_any']), schema_version=value['schema_version'],
                   excluded_creature_subtype=value.get('excluded_creature_subtype'),
                   mana_value_max=value.get('mana_value_max'))

    @property
    def query(self):
        if self.returns_to_battlefield:
            return ObjectQuerySpec(zones=('graveyard',), types_any=self.types_any,
                                   token=False, mana_value_max=self.mana_value_max)
        return ObjectQuerySpec(zones=('graveyard',), types_any=self.types_any)

    def matches(self, row) -> bool:
        return object_matches_query(row, self.query) and (
            self.excluded_creature_subtype is None
            or 'planeswalker' in row.types
            or self.excluded_creature_subtype not in row.subtypes
        )

    def public_set(self):
        return PublicZoneMoveSetSpec(
            self.query, PublicZoneOrigin.GRAVEYARD,
            PublicZoneDestination.BATTLEFIELD if self.returns_to_battlefield else PublicZoneDestination.OWNER_HAND,
            relation_axis=PublicZoneRelationAxis.OWNER, seat_relation=PublicZoneSeatRelation.ACTOR,
            schema_version=2, battlefield_controller=(
                PublicBattlefieldController.OWNER if self.returns_to_battlefield else None
            ),
        )


def mandatory_creature_or_planeswalker_return_effect():
    selection = GraveyardReturnSelection(
        _CREATURE_OR_PLANESWALKER, schema_version=MANDATORY_CREATURE_OR_PLANESWALKER_VERSION,
    )
    return {'op': OPERATION, 'player': '$controller', 'selection': selection.to_dict()}


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
    rows = None
    if selection.excluded_creature_subtype is not None:
        rows = tuple(row for row in host.public_zone_move_object_rows(actor) if selection.matches(row))
    snapshot = snapshot_public_zone_move_set(
        host, actor=actor, spec=selection.public_set(), object_rows=rows,
    )
    return tuple(value.ref for value in snapshot.objects
        if host.state.cards[value.object_id].is_card_object)


def graveyard_return_choice_node_capabilities(*, effects, target_schema, mechanic_ids, **kwargs):
    if MECHANIC not in mechanic_ids or target_schema is not None or len(effects) != 1:
        return ()
    if is_paired_graveyard_return(effects[0]):
        effect = effects[0]
        marker = effect[RETURN_PAIR_FIELD]
        if (effect != paired_graveyard_return_effect() or type(marker["schema_version"]) is not int):
            return ()
        return PAIRED_RETURN_CAPABILITIES
    try:
        selection = validate_return_choice(effects[0])
    except (TypeError, ValueError):
        return ()
    if selection.returns_to_battlefield:
        return (CAPABILITY, BOUNDED_BATTLEFIELD_RETURN_CAPABILITY)
    if selection.greatest_power:
        return (CAPABILITY, GREATEST_POWER_CAPABILITY)
    if selection.excluded_creature_subtype is not None:
        return (CAPABILITY, QUALIFIED_CHOICE_CAPABILITY)
    return (CAPABILITY,)


def graveyard_return_choice_runtime_dependencies(program):
    def walk(value):
        if isinstance(value, Mapping):
            if is_paired_graveyard_return(value):
                from ..controller_creature_keyword_absence import is_controller_end_step_schedule

                required = graveyard_return_choice_node_capabilities(
                    effects=(value,), target_schema=program.target_schema, mechanic_ids=program.coverage,
                )
                if (
                    not required or (program.event, program.active_zone) != ("step.begin", "battlefield")
                    or program.handlers or tuple(program.effects) != (value,)
                    or not is_controller_end_step_schedule(program.event_condition)
                ):
                    raise ValueError("Paired graveyard return requires its original controller end-step choice")
                return set(required)
            if value.get('op') == OPERATION:
                required = graveyard_return_choice_node_capabilities(
                    effects=(value,), target_schema=None, mechanic_ids=program.coverage)
                if not required or (
                    BOUNDED_BATTLEFIELD_RETURN_CAPABILITY in required and program.target_schema is not None
                ):
                    raise ValueError('Graveyard choice lacks a closed owned instruction')
                return set(required)
            return {cap for child in value.values() for cap in walk(child)}
        if isinstance(value, (tuple, list)):
            return {cap for child in value for cap in walk(child)}
        return set()
    return tuple(sorted(walk((program.effects, program.handlers))))
