from __future__ import annotations

"""Typed public-origin zone-move effects.

This module selects immutable public object sets and delegates every mutation,
replacement, incarnation, trigger, and journal transition to the canonical
``ZoneTransitionOwner``.
"""

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .object_predicate import ObjectQueryError, ObjectQuerySpec
from .rules.different_name_graveyard_return import (
    CHOSEN_MOVE_VERSION as PUBLIC_CHOSEN_DIFFERENT_NAME_RETURN_VERSION, PERMANENT_QUERY,
    chosen_permanents_have_different_names, graveyard_front_faces,
)
from .object_query import ObjectQueryResult, query_objects
from .target_numeric import TargetNumericCharacteristic, TargetNumericCharacteristicSpec, TargetNumericComparison
from .rules.committed_quantity_followups import CommittedQuantityFollowup
from .rules.public_exile_result_model import PublicExileFollowup
from .rules.single_object_zone_transition import (
    SingleObjectDestination,
    SingleObjectOrigin,
    SingleObjectZoneTransitionError,
    SingleObjectZoneTransitionResult,
    commit_prevalidated_single_object_zone_transition,
    prepare_single_object_zone_transition,
    request_for_card,
)
from .util import stable_json


FIXED_OWNER_ZONE_MOVE_CAPABILITY = "zone.single_owner_move.fixed_destination"
FIXED_OWNER_ZONE_MOVE_MECHANIC = "fixed-owner-zone-move"
OWNER_LIBRARY_POSITION_OPERATION = "choose_owner_library_position"
OWNER_LIBRARY_POSITION_MECHANIC = "owner-choice-of-library-position"
OWNER_LIBRARY_POSITION_CAPABILITY = "choice.target_owner.library_position"
GRAVEYARD_LIBRARY_TARGET_CAPABILITY = "zone.single_owner_move.graveyard_library_target"
GRAVEYARD_LIBRARY_TARGET_FIELD = "graveyard_library_target_version"


def owned_permanent_library_bottom_target_schema() -> dict[str, Any]:
    return {"zones": ["battlefield"], "categories": ["permanent"], "count": 1, "owner_relation": "you"}


STACK_SPELL_EXILE_FIELD = "stack_spell_exile"


class StackSpellExileScope(str, Enum):
    SELECTED = "selected_spells"
    OTHER_SPELLS_AND_ABILITIES = "other_spells_then_all_abilities"


@dataclass(frozen=True, slots=True)
class StackSpellExileSpec:
    scope: StackSpellExileScope
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1 or not isinstance(self.scope, StackSpellExileScope):
            raise ValueError("Stack exile requires the exact spell scope")

    def to_dict(self):
        return {"schema_version": self.schema_version, "scope": self.scope.value}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, Mapping) or set(data) != {"schema_version", "scope"}:
            raise ValueError("Stack exile descriptor fields are closed")
        return cls(StackSpellExileScope(data["scope"]), data["schema_version"])

    @property
    def target_schema(self):
        if self.scope is not StackSpellExileScope.SELECTED:
            return None
        from .target_cardinality import AnnouncedTargetCardinality

        return {
            "zones": ["stack"], "categories": ["spell"], "source_exclusion": True,
            "count_parameter": AnnouncedTargetCardinality("up_to", "any_number", schema_version=2).to_dict(),
        }

    def effect(self):
        fields = {"op": "exile", STACK_SPELL_EXILE_FIELD: self.to_dict()}
        if self.scope is StackSpellExileScope.SELECTED:
            fields["stacks"] = "$targets"
        return fields

    @property
    def capability_dependencies(self):
        if self.scope is StackSpellExileScope.SELECTED:
            from .target_cardinality import ANY_NUMBER_TARGET_CAPABILITY

            return ("zone.change.destination_replacement", "target.revalidate_resolution",
                    "resolution.effect.fixed_homogeneous_target_set", ANY_NUMBER_TARGET_CAPABILITY)
        return ("zone.change.destination_replacement", "stack.counter.effect")


def _checked_stack_spell_exile(effects, target_schema):
    from .replacement.immutable import thaw_value

    if len(effects) != 1 or not isinstance(effects[0], Mapping):
        raise ValueError("Stack exile requires one complete effect")
    effect = thaw_value(effects[0])
    spec = StackSpellExileSpec.from_dict(effect.get(STACK_SPELL_EXILE_FIELD))
    if effect != spec.effect() or thaw_value(target_schema) != spec.target_schema:
        raise ValueError("Stack exile requires its exact source and target scope")
    return spec


def stack_spell_exile_node_capabilities(*, effects, target_schema, mechanic_ids):
    if not any(STACK_SPELL_EXILE_FIELD in effect for effect in effects):
        return ()
    try:
        return _checked_stack_spell_exile(effects, target_schema).capability_dependencies
    except (ValueError, TypeError):
        return ()


def stack_spell_exile_runtime_dependencies(program):
    from .rules.effect_shape_walkers import nested_effects

    effects = (*program.effects, *getattr(program, "handlers", ()))
    if not any(STACK_SPELL_EXILE_FIELD in effect for effect in nested_effects(effects)):
        return ()
    if ((program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:") or getattr(program, "handlers", ())):
        raise ValueError("Stack exile requires its complete spell resolution")
    return _checked_stack_spell_exile(program.effects, program.target_schema).capability_dependencies


def is_closed_stack_spell_exile_program(program):
    try:
        required = stack_spell_exile_runtime_dependencies(program)
    except (ValueError, TypeError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


class PublicZoneMoveError(ValueError):
    """A public zone-move descriptor, snapshot, or request is invalid."""


OWN_GRAVEYARD_TYPE_CHOICE_FIELD = "own_graveyard_type_choice_version"
OWN_GRAVEYARD_TYPE_RETURN_CAPABILITY = "zone.return.chosen_permanent_type_own_graveyard"


def own_graveyard_type_return_choice_effect() -> dict[str, Any]:
    from .external_entry_state_model import PERMANENT_CARD_TYPES

    types = sorted(PERMANENT_CARD_TYPES)
    branches = {}
    for card_type in types:
        spec = PublicZoneMoveSetSpec(
            query=ObjectQuerySpec(zones=("graveyard",), types_all=(card_type,), token=False),
            origin=PublicZoneOrigin.GRAVEYARD,
            destination=PublicZoneDestination.OWNER_HAND,
            relation_axis=PublicZoneRelationAxis.OWNER,
            seat_relation=PublicZoneSeatRelation.ACTOR,
            schema_version=2,
        )
        branches[card_type] = [{"op": "move_public_zone_set", "source": "$source", "set": spec.to_dict()}]
    return {
        "op": "choose_option",
        OWN_GRAVEYARD_TYPE_CHOICE_FIELD: 1,
        "player": "$controller",
        "prompt": "Choose a permanent type.",
        "options": [{"id": card_type, "label": card_type.title()} for card_type in types],
        "then_by_choice": branches,
    }


def own_graveyard_type_return_dependencies(value: Mapping[str, Any]) -> tuple[str, ...]:
    if OWN_GRAVEYARD_TYPE_CHOICE_FIELD not in value:
        return ()
    from .replacement.immutable import thaw_value

    if (type(value[OWN_GRAVEYARD_TYPE_CHOICE_FIELD]) is not int
            or thaw_value(value) != own_graveyard_type_return_choice_effect()):
        raise PublicZoneMoveError("Permanent-type graveyard return requires the six exact owner-hand branches")
    return ("zone.move.fixed_public_set", OWN_GRAVEYARD_TYPE_RETURN_CAPABILITY)


MIXED_ZONE_EXILE_FIELD = "mixed_zone_exile"
MIXED_ZONE_EXILE_CAPABILITY = "zone.exile.fixed_mixed_sets"


class MixedZoneExileScope(str, Enum):
    HANDS_AND_GRAVEYARDS = "hands_and_graveyards"
    CONTROLLER_HAND_DRAW_AND_PLAY = "controller_hand_draw_and_play"
    ARTIFACTS_CREATURES_LANDS_HANDS_AND_GRAVEYARDS = "artifacts_creatures_lands_hands_and_graveyards"


@dataclass(frozen=True, slots=True)
class FixedMixedZoneExileSpec:
    scope: MixedZoneExileScope

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MixedZoneExileScope):
            raise PublicZoneMoveError("Mixed-zone exile requires one closed affected set")

    def to_dict(self) -> dict[str, Any]:
        version = 2 if self.scope is MixedZoneExileScope.CONTROLLER_HAND_DRAW_AND_PLAY else 1
        return {"schema_version": version, MIXED_ZONE_EXILE_FIELD: self.scope.value}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FixedMixedZoneExileSpec:
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", MIXED_ZONE_EXILE_FIELD}
                or type(value["schema_version"]) is not int or type(value[MIXED_ZONE_EXILE_FIELD]) is not str):
            raise PublicZoneMoveError("Mixed-zone exile uses a closed versioned scope")
        try:
            spec = cls(MixedZoneExileScope(value[MIXED_ZONE_EXILE_FIELD]))
        except ValueError as exc:
            raise PublicZoneMoveError("Mixed-zone exile scope is unsupported") from exc
        if spec.to_dict() != dict(value):
            raise PublicZoneMoveError("Mixed-zone exile scope requires its original descriptor version")
        return spec

    def effect(self) -> dict[str, Any]:
        return {"op": "move_public_zone_set", "source": "$source", "set": self.to_dict()}


def is_mixed_zone_exile_spec(value: Any) -> bool:
    return isinstance(value, Mapping) and MIXED_ZONE_EXILE_FIELD in value


class PublicZoneOrigin(str, Enum):
    BATTLEFIELD = "battlefield"
    GRAVEYARD = "graveyard"


class PublicZoneDestination(str, Enum):
    EXILE = "exile"
    OWNER_HAND = "hand"
    BATTLEFIELD = "battlefield"


class PublicBattlefieldController(str, Enum):
    OWNER = "owner"
    ACTOR = "actor"


class PublicZoneRelationAxis(str, Enum):
    CONTROLLER = "controller"
    OWNER = "owner"


class PublicZoneSeatRelation(str, Enum):
    ANY = "any"
    ACTOR = "actor"
    OPPONENTS = "opponents"
    TARGET_PLAYER = "target_player"


PUBLIC_AURA_ENTRY_VERSION = 3
ANNOUNCED_GRAVEYARD_RETURN_VERSION = 5
SAME_TURN_GRAVEYARD_RETURN_VERSION = 7
TARGETED_SAME_TURN_GRAVEYARD_RETURN_VERSION = 8
PUBLIC_CHOSEN_GRAVEYARD_RETURN_VERSION = 9
PUBLIC_CHOSEN_GRAVEYARD_TAPPED_LAND_VERSION = 10
PUBLIC_EACH_GRAVEYARD_CHOSEN_RETURN_VERSION = 11
PUBLIC_CHOSEN_GRAVEYARD_RETURN_VERSIONS = (
    PUBLIC_CHOSEN_GRAVEYARD_RETURN_VERSION, PUBLIC_CHOSEN_GRAVEYARD_TAPPED_LAND_VERSION,
    PUBLIC_EACH_GRAVEYARD_CHOSEN_RETURN_VERSION,
    PUBLIC_CHOSEN_DIFFERENT_NAME_RETURN_VERSION,
)
SAME_TURN_GRAVEYARD_RETURN_VERSIONS = (SAME_TURN_GRAVEYARD_RETURN_VERSION, TARGETED_SAME_TURN_GRAVEYARD_RETURN_VERSION)
PUBLIC_AURA_ENTRY_VERSIONS = (
    PUBLIC_AURA_ENTRY_VERSION, ANNOUNCED_GRAVEYARD_RETURN_VERSION, *SAME_TURN_GRAVEYARD_RETURN_VERSIONS,
    *PUBLIC_CHOSEN_GRAVEYARD_RETURN_VERSIONS,
)
ANNOUNCED_GRAVEYARD_RETURN_CAPABILITY = "zone.move.announced_owned_graveyard_return"
ANNOUNCED_MANA_VALUE_MAX_FIELD = "announced_mana_value_max"
PUBLIC_GRAVEYARD_POWER_RETURN_VERSION = 4
PUBLIC_SAME_NAME_MOVE_VERSION = 6
PUBLIC_NONHISTORIC_NONLAND_RETURN_VERSION = 12
NONHISTORIC_NONLAND_RETURN_CAPABILITY = "zone.move.nonhistoric_nonland_to_owner_hand"
PUBLIC_SAME_NAME_MOVE_TARGET_FIELD = "same_name_target"
PUBLIC_GRAVEYARD_POWER_RETURN_CAPABILITY = "zone.return.own_graveyard_creature_power_set"


NONHISTORIC_NONLAND_QUERY = ObjectQuerySpec(
    zones=("battlefield",), excluded_types=("artifact", "land"),
    excluded_subtypes=("saga",), excluded_supertypes=("legendary",),
)


_GRAVEYARD_CREATURE_CARD_QUERY = ObjectQuerySpec(zones=("graveyard",), types_all=("creature",), token=False)


def public_graveyard_power_return_spec(maximum_power: int) -> PublicZoneMoveSetSpec:
    return PublicZoneMoveSetSpec(
        query=_GRAVEYARD_CREATURE_CARD_QUERY,
        origin=PublicZoneOrigin.GRAVEYARD,
        destination=PublicZoneDestination.OWNER_HAND,
        relation_axis=PublicZoneRelationAxis.OWNER,
        seat_relation=PublicZoneSeatRelation.ACTOR,
        schema_version=PUBLIC_GRAVEYARD_POWER_RETURN_VERSION,
        graveyard_power=TargetNumericCharacteristicSpec(
            TargetNumericCharacteristic.POWER, TargetNumericComparison.AT_MOST, maximum_power,
        ),
    )


def _same_turn_graveyard_return_query(seat_relation):
    from .external_entry_state_model import PERMANENT_CARD_TYPES

    types = (
        tuple(sorted(PERMANENT_CARD_TYPES)) if seat_relation is PublicZoneSeatRelation.ACTOR
        else ("artifact", "creature", "enchantment", "land")
    )
    return ObjectQuerySpec(zones=("graveyard",), types_any=types, token=False)


def same_turn_graveyard_return_spec(seat_relation, *, targeted=False):
    version = TARGETED_SAME_TURN_GRAVEYARD_RETURN_VERSION if targeted else SAME_TURN_GRAVEYARD_RETURN_VERSION
    return PublicZoneMoveSetSpec(
        query=_same_turn_graveyard_return_query(seat_relation),
        origin=PublicZoneOrigin.GRAVEYARD, destination=PublicZoneDestination.BATTLEFIELD,
        relation_axis=PublicZoneRelationAxis.OWNER, seat_relation=seat_relation,
        battlefield_controller=PublicBattlefieldController.OWNER, schema_version=version,
        battlefield_tapped=targeted, target_refs="$targets" if targeted else None,
    )


def targeted_same_turn_graveyard_return_schema():
    from .target_history import GRAVEYARD_BATTLEFIELD_ARRIVAL_FIELD

    return {
        "zones": ["graveyard"], "categories": ["card"], "owner_relation": "you", "up_to": 2,
        "types_any": list(_same_turn_graveyard_return_query(PublicZoneSeatRelation.ACTOR).types_any),
        "token": False, GRAVEYARD_BATTLEFIELD_ARRIVAL_FIELD: True,
    }


def same_turn_graveyard_return_history_query(seat_relation):
    from .public_history_query_model import PublicHistoryQuerySpec

    return PublicHistoryQuerySpec(
        "card_arrived_graveyard", relation="controller" if seat_relation is PublicZoneSeatRelation.ACTOR else "any_player",
        schema_version=2, origin="battlefield",
    )


_SET_FIELDS_V1 = frozenset(
    {
        "schema_version",
        "origin",
        "destination",
        "relation_axis",
        "seat_relation",
        "target_seat",
        "exclude_source",
        "query",
    }
)
_SET_FIELDS_V2 = _SET_FIELDS_V1 | frozenset(
    {"battlefield_controller", "battlefield_tapped"}
)
_SET_FIELDS_POWER_RETURN = _SET_FIELDS_V2 | {"graveyard_power"}
_SET_FIELDS_ANNOUNCED_RETURN = _SET_FIELDS_V2 | {ANNOUNCED_MANA_VALUE_MAX_FIELD}
_SET_FIELDS_SAME_NAME = _SET_FIELDS_V1 | {PUBLIC_SAME_NAME_MOVE_TARGET_FIELD}
_SET_FIELDS_TARGETED_SAME_TURN = _SET_FIELDS_V2 | {"target_refs"}
_SET_FIELDS_CHOSEN_RETURN = _SET_FIELDS_V2 | {"chosen_cards"}
_CHOSEN_CREATURE_CARD_QUERY = ObjectQuerySpec(zones=("graveyard",), types_all=("creature",), token=False)
_CHOSEN_LAND_CARD_QUERY = ObjectQuerySpec(zones=("graveyard",), types_all=("land",), token=False)


def _nonempty(value: Any, *, field: str) -> str:
    if type(value) is not str or not value:
        raise PublicZoneMoveError(f"{field} must be a nonempty string")
    return value


@dataclass(frozen=True, slots=True)
class PublicZoneMoveSetSpec:
    """One closed public-origin object set and requested destination."""

    query: ObjectQuerySpec
    origin: PublicZoneOrigin
    destination: PublicZoneDestination
    relation_axis: PublicZoneRelationAxis = PublicZoneRelationAxis.CONTROLLER
    seat_relation: PublicZoneSeatRelation = PublicZoneSeatRelation.ANY
    target_seat: str | None = None
    exclude_source: bool = False
    battlefield_controller: PublicBattlefieldController | None = None
    battlefield_tapped: bool = False
    schema_version: int = 1
    graveyard_power: TargetNumericCharacteristicSpec | None = None
    announced_mana_value_max: int | None = None
    same_name_target: str | None = None
    target_refs: tuple[str, ...] | str | None = None
    chosen_cards: tuple[PublicZoneMoveObject, ...] = ()

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {
            1, 2, *PUBLIC_AURA_ENTRY_VERSIONS, PUBLIC_GRAVEYARD_POWER_RETURN_VERSION, PUBLIC_SAME_NAME_MOVE_VERSION,
            PUBLIC_NONHISTORIC_NONLAND_RETURN_VERSION,
        }:
            raise PublicZoneMoveError(
                "Unsupported public zone-move set schema version"
            )
        if not isinstance(self.query, ObjectQuerySpec):
            raise PublicZoneMoveError(
                "Public zone-move sets require a typed object query"
            )
        if not isinstance(self.origin, PublicZoneOrigin) or not isinstance(
            self.destination, PublicZoneDestination
        ):
            raise PublicZoneMoveError(
                "Public zone-move origin or destination is unsupported"
            )
        if not isinstance(self.relation_axis, PublicZoneRelationAxis) or not isinstance(
            self.seat_relation, PublicZoneSeatRelation
        ):
            raise PublicZoneMoveError(
                "Public zone-move seat relation is unsupported"
            )
        if self.query.zones != (self.origin.value,):
            raise PublicZoneMoveError(
                "Public zone-move query must name its exact origin"
            )
        if self.query.owner is not None or self.query.controller is not None:
            raise PublicZoneMoveError(
                "Public zone-move sets use the typed seat relation"
            )
        if self.query.known_to_actor is not None or self.query.exclude_ref is not None:
            raise PublicZoneMoveError(
                "Public zone-move queries cannot use knowledge or reference shortcuts"
            )
        if self.query.include_phased_out:
            raise PublicZoneMoveError(
                "Public zone-move sets exclude phased-out objects"
            )
        if self.origin is PublicZoneOrigin.GRAVEYARD:
            if self.relation_axis is not PublicZoneRelationAxis.OWNER:
                raise PublicZoneMoveError(
                    "Graveyard sets use owner-relative seats"
                )
        if type(self.exclude_source) is not bool:
            raise PublicZoneMoveError(
                "Public zone-move source exclusion must be boolean"
            )
        if self.origin is PublicZoneOrigin.GRAVEYARD and self.exclude_source:
            raise PublicZoneMoveError(
                "Graveyard set grammar does not exclude the resolving source"
            )
        if type(self.battlefield_tapped) is not bool:
            raise PublicZoneMoveError(
                "Public zone-move battlefield tapped policy must be boolean"
            )
        if self.destination is PublicZoneDestination.BATTLEFIELD:
            if (
                self.schema_version not in {2, *PUBLIC_AURA_ENTRY_VERSIONS}
                or self.origin is not PublicZoneOrigin.GRAVEYARD
                or not isinstance(
                    self.battlefield_controller,
                    PublicBattlefieldController,
                )
            ):
                raise PublicZoneMoveError(
                    "Public battlefield entry requires a versioned graveyard set and controller policy"
                )
        elif self.battlefield_controller is not None or self.battlefield_tapped:
            raise PublicZoneMoveError(
                "Only battlefield destinations accept entry policies"
            )
        if self.schema_version == PUBLIC_GRAVEYARD_POWER_RETURN_VERSION:
            self._validate_graveyard_power_return()
        elif self.graveyard_power is not None:
            raise PublicZoneMoveError("Graveyard power return requires its explicit set version")
        if self.schema_version == ANNOUNCED_GRAVEYARD_RETURN_VERSION:
            self._validate_announced_graveyard_return()
        elif self.announced_mana_value_max is not None:
            raise PublicZoneMoveError("An announced mana-value return requires its explicit set version")
        if self.schema_version == PUBLIC_AURA_ENTRY_VERSION and self.destination is not PublicZoneDestination.BATTLEFIELD:
            raise PublicZoneMoveError("Public zone-move version three is reserved for Aura-capable battlefield entry")
        if (
            self.schema_version == 1
            and self.origin is PublicZoneOrigin.GRAVEYARD
            and self.destination is not PublicZoneDestination.EXILE
        ):
            raise PublicZoneMoveError(
                "Historical public graveyard sets move only to exile"
            )
        if self.schema_version == 1 and (
            self.battlefield_controller is not None or self.battlefield_tapped
        ):
            raise PublicZoneMoveError(
                "Historical public zone-move sets cannot carry entry policies"
            )
        if self.seat_relation is PublicZoneSeatRelation.TARGET_PLAYER:
            _nonempty(self.target_seat, field="Public zone-move target seat")
        elif self.target_seat is not None:
            raise PublicZoneMoveError(
                "Only target-player sets accept a target seat"
            )

        if self.schema_version == PUBLIC_NONHISTORIC_NONLAND_RETURN_VERSION:
            if (self.query != NONHISTORIC_NONLAND_QUERY or self.origin is not PublicZoneOrigin.BATTLEFIELD
                    or self.destination is not PublicZoneDestination.OWNER_HAND
                    or self.relation_axis is not PublicZoneRelationAxis.CONTROLLER
                    or self.seat_relation is not PublicZoneSeatRelation.ANY or self.exclude_source):
                raise PublicZoneMoveError("Nonhistoric return requires the complete untargeted nonland battlefield set")
        self._validate_same_name_target()
        if self.schema_version in SAME_TURN_GRAVEYARD_RETURN_VERSIONS:
            self._validate_same_turn_graveyard_return()
        elif self.target_refs is not None:
            raise PublicZoneMoveError("Original graveyard targets require the exact versioned return")
        if self.schema_version in PUBLIC_CHOSEN_GRAVEYARD_RETURN_VERSIONS:
            self._validate_chosen_graveyard_return()
        elif self.chosen_cards:
            raise PublicZoneMoveError("Chosen creature cards require the exact owner-return version")

    def _validate_chosen_graveyard_return(self):
        cards = self.chosen_cards
        if self.schema_version == PUBLIC_EACH_GRAVEYARD_CHOSEN_RETURN_VERSION:
            if (
                self.origin is not PublicZoneOrigin.GRAVEYARD or self.destination is not PublicZoneDestination.BATTLEFIELD
                or self.relation_axis is not PublicZoneRelationAxis.OWNER or self.seat_relation is not PublicZoneSeatRelation.ANY
                or self.battlefield_controller is not PublicBattlefieldController.ACTOR or self.battlefield_tapped
                or self.query != ObjectQuerySpec(zones=("graveyard",), types_any=("creature", "planeswalker"), token=False)
                or not isinstance(cards, (tuple, list)) or not cards
                or any(not isinstance(card, PublicZoneMoveObject) for card in cards)
                or len({card.object_id for card in cards}) != len(cards)
                or len({card.owner for card in cards}) != len(cards)
            ):
                raise PublicZoneMoveError("Choose one creature or planeswalker card per represented graveyard")
            object.__setattr__(self, "chosen_cards", tuple(cards))
            return
        if self.schema_version == PUBLIC_CHOSEN_DIFFERENT_NAME_RETURN_VERSION:
            self._validate_different_name_graveyard_return(cards)
            return
        tapped_land = self.schema_version == PUBLIC_CHOSEN_GRAVEYARD_TAPPED_LAND_VERSION
        query = _CHOSEN_LAND_CARD_QUERY if tapped_land else _CHOSEN_CREATURE_CARD_QUERY
        relation = PublicZoneSeatRelation.ACTOR if tapped_land else PublicZoneSeatRelation.ANY
        allowed_counts = (1,) if tapped_land else (1, 2)
        if (
            self.origin is not PublicZoneOrigin.GRAVEYARD or self.destination is not PublicZoneDestination.BATTLEFIELD
            or self.relation_axis is not PublicZoneRelationAxis.OWNER or self.seat_relation is not relation
            or self.battlefield_controller is not PublicBattlefieldController.OWNER or self.battlefield_tapped is not tapped_land
            or self.query != query or not isinstance(cards, (tuple, list))
            or len(cards) not in allowed_counts or any(not isinstance(card, PublicZoneMoveObject) for card in cards)
            or len({card.object_id for card in cards}) != len(cards)
            or len({card.owner for card in cards}) != len(cards)
        ):
            message = ("Chosen land return requires one exact original land card in the actor's graveyard"
                       if tapped_land else "Chosen creature return requires one exact original card per graveyard owner")
            raise PublicZoneMoveError(message)
        object.__setattr__(self, "chosen_cards", tuple(cards))

    def _validate_different_name_graveyard_return(self, cards):
        if (self.query != PERMANENT_QUERY
                or self.origin is not PublicZoneOrigin.GRAVEYARD
                or self.destination is not PublicZoneDestination.BATTLEFIELD
                or self.relation_axis is not PublicZoneRelationAxis.OWNER
                or self.seat_relation is not PublicZoneSeatRelation.ACTOR
                or self.battlefield_controller is not PublicBattlefieldController.OWNER
                or self.battlefield_tapped or self.exclude_source
                or not isinstance(cards, (tuple, list)) or not cards
                or any(not isinstance(card, PublicZoneMoveObject) for card in cards)
                or len({card.object_id for card in cards}) != len(cards)
                or len({card.owner for card in cards}) != 1):
            raise PublicZoneMoveError("Different-name return requires a sealed actor-owned permanent card set")
        object.__setattr__(self, "chosen_cards", tuple(cards))

    def _validate_same_turn_graveyard_return(self) -> None:
        targeted = self.schema_version == TARGETED_SAME_TURN_GRAVEYARD_RETURN_VERSION
        if (
            self.origin is not PublicZoneOrigin.GRAVEYARD or self.destination is not PublicZoneDestination.BATTLEFIELD
            or self.relation_axis is not PublicZoneRelationAxis.OWNER
            or self.seat_relation not in {PublicZoneSeatRelation.ACTOR, PublicZoneSeatRelation.ANY}
            or self.battlefield_controller is not PublicBattlefieldController.OWNER
            or self.battlefield_tapped is not targeted or (targeted and self.seat_relation is not PublicZoneSeatRelation.ACTOR)
            or self.query != _same_turn_graveyard_return_query(self.seat_relation)
        ):
            raise PublicZoneMoveError("Same-turn graveyard return requires its exact owner scope and current card types")
        if not targeted:
            if self.target_refs is not None:
                raise PublicZoneMoveError("The untargeted arrival return uses its full current set")
            return
        refs = self.target_refs
        if refs == "$targets":
            return
        if (
            not isinstance(refs, (tuple, list)) or len(refs) > 2
            or any(type(ref) is not str or not ref or ref.startswith("$") for ref in refs)
            or len(set(refs)) != len(refs)
        ):
            raise PublicZoneMoveError("A targeted arrival return requires zero to two distinct revalidated references")
        object.__setattr__(self, "target_refs", tuple(refs))

    def _validate_same_name_target(self) -> None:
        if self.schema_version == PUBLIC_SAME_NAME_MOVE_VERSION:
            from .rules.same_name_zone_moves import same_name_zone_move_scope

            same_name_zone_move_scope(self)
        elif self.same_name_target is not None:
            raise PublicZoneMoveError("Same-name targets require their explicit set version")

    def _validate_graveyard_power_return(self) -> None:
        maximum = self.graveyard_power
        if (
            self.origin is not PublicZoneOrigin.GRAVEYARD
            or self.destination is not PublicZoneDestination.OWNER_HAND
            or self.relation_axis is not PublicZoneRelationAxis.OWNER
            or self.seat_relation is not PublicZoneSeatRelation.ACTOR
            or self.query != _GRAVEYARD_CREATURE_CARD_QUERY
            or not isinstance(maximum, TargetNumericCharacteristicSpec)
            or maximum.characteristic is not TargetNumericCharacteristic.POWER
            or maximum.comparison is not TargetNumericComparison.AT_MOST
        ):
            raise PublicZoneMoveError("Graveyard power return requires the exact own creature-card set")

    def _validate_announced_graveyard_return(self) -> None:
        expected = ObjectQuerySpec(zones=("graveyard",), types_any=("creature", "planeswalker"), token=False)
        if (
            self.origin is not PublicZoneOrigin.GRAVEYARD
            or self.destination is not PublicZoneDestination.BATTLEFIELD
            or self.relation_axis is not PublicZoneRelationAxis.OWNER
            or self.seat_relation is not PublicZoneSeatRelation.ACTOR
            or self.battlefield_controller is not PublicBattlefieldController.OWNER
            or self.battlefield_tapped
            or self.query != expected
            or type(self.announced_mana_value_max) is not int
            or self.announced_mana_value_max < 0
        ):
            raise PublicZoneMoveError("An announced return requires the exact owned creature and planeswalker set")


    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "origin": self.origin.value,
            "destination": self.destination.value,
            "relation_axis": self.relation_axis.value,
            "seat_relation": self.seat_relation.value,
            "target_seat": self.target_seat,
            "exclude_source": self.exclude_source,
            "query": self.query.canonical_dict(),
        }
        if self.schema_version in {2, *PUBLIC_AURA_ENTRY_VERSIONS, PUBLIC_GRAVEYARD_POWER_RETURN_VERSION}:
            value.update(
                {
                    "battlefield_controller": (
                        self.battlefield_controller.value
                        if self.battlefield_controller is not None
                        else None
                    ),
                    "battlefield_tapped": self.battlefield_tapped,
                }
            )
        if self.graveyard_power is not None:
            value["graveyard_power"] = self.graveyard_power.to_dict()
        if self.announced_mana_value_max is not None:
            value[ANNOUNCED_MANA_VALUE_MAX_FIELD] = self.announced_mana_value_max
        if self.same_name_target is not None:
            value[PUBLIC_SAME_NAME_MOVE_TARGET_FIELD] = self.same_name_target
        if self.target_refs is not None:
            value["target_refs"] = list(self.target_refs) if isinstance(self.target_refs, tuple) else self.target_refs
        if self.chosen_cards:
            value["chosen_cards"] = [card.to_dict() for card in self.chosen_cards]
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PublicZoneMoveSetSpec":
        if not isinstance(value, Mapping) or frozenset(value) not in {
            _SET_FIELDS_V1,
            _SET_FIELDS_V2,
            _SET_FIELDS_POWER_RETURN,
            _SET_FIELDS_ANNOUNCED_RETURN,
            _SET_FIELDS_SAME_NAME,
            _SET_FIELDS_TARGETED_SAME_TURN,
            _SET_FIELDS_CHOSEN_RETURN,
        } or ("graveyard_power" in value) != (value["schema_version"] == PUBLIC_GRAVEYARD_POWER_RETURN_VERSION) or (
            ANNOUNCED_MANA_VALUE_MAX_FIELD in value
        ) != (value["schema_version"] == ANNOUNCED_GRAVEYARD_RETURN_VERSION) or (
            (frozenset(value) == _SET_FIELDS_SAME_NAME) != (value["schema_version"] == PUBLIC_SAME_NAME_MOVE_VERSION)
        ) or (
            (frozenset(value) == _SET_FIELDS_TARGETED_SAME_TURN)
            != (value["schema_version"] == TARGETED_SAME_TURN_GRAVEYARD_RETURN_VERSION)
        ) or (
            (frozenset(value) == _SET_FIELDS_CHOSEN_RETURN)
            != (value["schema_version"] in PUBLIC_CHOSEN_GRAVEYARD_RETURN_VERSIONS)
        ) or (
            value["schema_version"] == PUBLIC_NONHISTORIC_NONLAND_RETURN_VERSION
            and frozenset(value) != _SET_FIELDS_V1
        ):
            raise PublicZoneMoveError(
                "Public zone-move set fields are incomplete or unknown"
            )
        try:
            return cls(
                query=ObjectQuerySpec.from_dict(value["query"]),
                origin=PublicZoneOrigin(value["origin"]),
                destination=PublicZoneDestination(value["destination"]),
                relation_axis=PublicZoneRelationAxis(value["relation_axis"]),
                seat_relation=PublicZoneSeatRelation(value["seat_relation"]),
                target_seat=value["target_seat"],
                exclude_source=value["exclude_source"],
                battlefield_controller=(
                    PublicBattlefieldController(value["battlefield_controller"])
                    if value.get("battlefield_controller") is not None
                    else None
                ),
                battlefield_tapped=value.get("battlefield_tapped", False),
                schema_version=value["schema_version"],
                graveyard_power=(TargetNumericCharacteristicSpec.from_mapping(value["graveyard_power"])
                                 if "graveyard_power" in value else None),
                announced_mana_value_max=value.get(ANNOUNCED_MANA_VALUE_MAX_FIELD),
                same_name_target=value.get(PUBLIC_SAME_NAME_MOVE_TARGET_FIELD),
                target_refs=value.get("target_refs"),
                chosen_cards=tuple(PublicZoneMoveObject.from_dict(row) for row in value.get("chosen_cards", ())),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ObjectQueryError,
        ) as exc:
            if isinstance(exc, PublicZoneMoveError):
                raise
            raise PublicZoneMoveError(
                "Public zone-move set descriptor is malformed"
            ) from exc

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            stable_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class PublicZoneMoveObject:
    object_id: str
    logical_object_id: str
    ref: str
    owner: str
    controller: str

    def __post_init__(self) -> None:
        for field, value in (
            ("object ID", self.object_id),
            ("logical object ID", self.logical_object_id),
            ("reference", self.ref),
            ("owner", self.owner),
            ("controller", self.controller),
        ):
            _nonempty(value, field=f"Public zone-move {field}")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"object_id", "logical_object_id", "ref", "owner", "controller"}:
            raise PublicZoneMoveError("A chosen public object must retain its exact identity and owner")
        return cls(**dict(value))

    @classmethod
    def from_row(cls, row):
        return cls(row.object_id, row.logical_object_id, row.ref, row.owner, row.controller)

    def matches_row(self, row):
        return (
            row is not None and row.object_id == self.object_id and row.logical_object_id == self.logical_object_id
            and row.ref == self.ref and row.owner == self.owner and row.controller == self.controller
        )


@dataclass(frozen=True, slots=True)
class PublicZoneMoveSnapshot:
    spec: PublicZoneMoveSetSpec
    objects: tuple[PublicZoneMoveObject, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.spec, PublicZoneMoveSetSpec):
            raise PublicZoneMoveError(
                "Public zone-move snapshots require a typed set"
            )
        values = tuple(self.objects)
        if any(not isinstance(value, PublicZoneMoveObject) for value in values):
            raise PublicZoneMoveError(
                "Public zone-move snapshots require typed objects"
            )
        object_ids = tuple(value.object_id for value in values)
        logical_ids = tuple(value.logical_object_id for value in values)
        if len(object_ids) != len(set(object_ids)) or len(logical_ids) != len(
            set(logical_ids)
        ):
            raise PublicZoneMoveError(
                "Public zone-move snapshots require unique identities"
            )
        object.__setattr__(self, "objects", values)

    @property
    def fingerprint(self) -> str:
        payload = {
            "spec": self.spec.to_dict(),
            "objects": [
                {
                    "object_id": value.object_id,
                    "logical_object_id": value.logical_object_id,
                    "ref": value.ref,
                    "owner": value.owner,
                    "controller": value.controller,
                }
                for value in self.objects
            ],
        }
        return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


class PublicZoneMoveQuery(Protocol):
    def public_zone_move_active_seats(self) -> tuple[str, ...]: ...

    def public_zone_move_apnap_order(self) -> tuple[str, ...]: ...

    def public_zone_move_object_rows(
        self, actor: str
    ) -> tuple[ObjectQueryResult, ...]: ...

    def public_zone_move_rules_names(self, row: ObjectQueryResult) -> tuple[str, ...]: ...


class PublicZoneMoveHost(PublicZoneMoveQuery, Protocol):
    state: Any

    def _resolve_object(
        self,
        actor: str,
        ref: str,
        *,
        zones: set[str] | None = None,
    ) -> Any: ...

    def move_card(self, object_id: str, destination: str, **kwargs: Any) -> Any: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        summary: str,
        details: Mapping[str, Any] | None = None,
        *,
        importance: int = 1,
        changed_objects: Sequence[str] = (),
        changed_players: Sequence[str] = (),
    ) -> None: ...


def _relation_seat(
    row: ObjectQueryResult,
    spec: PublicZoneMoveSetSpec,
) -> str:
    return (
        row.controller
        if spec.relation_axis is PublicZoneRelationAxis.CONTROLLER
        else row.owner
    )


def select_public_zone_move_objects(
    rows: Iterable[ObjectQueryResult],
    spec: PublicZoneMoveSetSpec,
    *,
    actor: str,
    active_seats: Iterable[str],
    apnap_order: Iterable[str],
    source_ref: str | None = None,
) -> tuple[ObjectQueryResult, ...]:
    """Select and APNAP-order one immutable public-origin object set."""

    if not isinstance(spec, PublicZoneMoveSetSpec):
        raise PublicZoneMoveError(
            "Public zone-move selection requires a typed set"
        )
    _nonempty(actor, field="Public zone-move actor")
    active = tuple(active_seats)
    order = tuple(apnap_order)
    if (
        actor not in active
        or len(active) != len(set(active))
        or len(order) != len(active)
        or set(order) != set(active)
    ):
        raise PublicZoneMoveError(
            "Public zone-move selection requires a complete APNAP view"
        )
    if spec.exclude_source:
        _nonempty(source_ref, field="Public zone-move source")
    order_index = {seat: index for index, seat in enumerate(order)}
    selected = tuple(
        row
        for row in query_objects(tuple(rows), spec.query)
        if row.zone == spec.origin.value
    )
    if spec.seat_relation is PublicZoneSeatRelation.ACTOR:
        selected = tuple(
            row for row in selected if _relation_seat(row, spec) == actor
        )
    elif spec.seat_relation is PublicZoneSeatRelation.OPPONENTS:
        selected = tuple(
            row for row in selected if _relation_seat(row, spec) != actor
        )
    elif spec.seat_relation is PublicZoneSeatRelation.TARGET_PLAYER:
        if spec.target_seat not in active:
            raise PublicZoneMoveError(
                "Public zone-move target player is no longer active"
            )
        selected = tuple(
            row
            for row in selected
            if _relation_seat(row, spec) == spec.target_seat
        )
    if spec.exclude_source:
        selected = tuple(row for row in selected if row.ref != source_ref)
    if spec.announced_mana_value_max is not None:
        selected = tuple(row for row in selected if row.mana_value <= spec.announced_mana_value_max)
    if spec.graveyard_power is not None:
        if any(type(row.effective_power) is not int for row in selected):
            raise PublicZoneMoveError("Graveyard power return requires exact current creature-card power")
        selected = tuple(row for row in selected if spec.graveyard_power.permits(
            power=row.effective_power, toughness=row.effective_toughness,
        ))
    if any(
        _relation_seat(row, spec) not in order_index
        or not row.object_id
        or not row.logical_object_id
        or not row.ref
        or row.phased_out
        for row in selected
    ):
        raise PublicZoneMoveError(
            "Public zone-move query returned an invalid identity"
        )
    by_logical_id: dict[str, ObjectQueryResult] = {}
    for row in selected:
        previous = by_logical_id.get(row.logical_object_id)
        if previous is not None and previous.object_id != row.object_id:
            raise PublicZoneMoveError(
                "Public zone-move query repeated one logical object"
            )
        by_logical_id[row.logical_object_id] = row
    return tuple(
        sorted(
            by_logical_id.values(),
            key=lambda row: (
                order_index[_relation_seat(row, spec)],
                row.logical_object_id,
                row.object_id,
                row.ref,
            ),
        )
    )


def snapshot_public_zone_move_set(
    query: PublicZoneMoveQuery,
    *,
    actor: str,
    spec: PublicZoneMoveSetSpec,
    source_ref: str | None = None,
    object_rows: Sequence[ObjectQueryResult] | None = None,
) -> PublicZoneMoveSnapshot:
    rows = query.public_zone_move_object_rows(actor) if object_rows is None else object_rows
    if spec.chosen_cards:
        choices = {card.object_id: card for card in spec.chosen_cards}
        rows = tuple(row for row in rows if row.object_id in choices and choices[row.object_id].matches_row(row))
    selected = select_public_zone_move_objects(
        rows,
        spec,
        actor=actor,
        active_seats=query.public_zone_move_active_seats(),
        apnap_order=query.public_zone_move_apnap_order(),
        source_ref=source_ref,
    )
    if spec.schema_version == PUBLIC_CHOSEN_DIFFERENT_NAME_RETURN_VERSION and (
            len(selected) != len(spec.chosen_cards) or any(not row.physical_card for row in selected)
            or not chosen_permanents_have_different_names(tuple(
                (row.ref, query.public_zone_move_rules_names(row)) for row in selected))):
        raise PublicZoneMoveError("The sealed group lost a graveyard incarnation or shares a rules name")
    if spec.same_name_target is not None:
        from .rules.same_name_zone_moves import select_same_name_zone_rows

        selected = select_same_name_zone_rows(query, spec, selected)
    return PublicZoneMoveSnapshot(
        spec=spec,
        objects=tuple(
            PublicZoneMoveObject(
                object_id=row.object_id,
                logical_object_id=row.logical_object_id,
                ref=row.ref,
                owner=row.owner,
                controller=row.controller,
            )
            for row in selected
        ),
    )


def _exile_followup_rows(host, actor, spec, followup):
    from .characteristic_query_boundary_model import PUBLIC_ZONE_FRONT_FACE_VERSION

    if not isinstance(followup, PublicExileFollowup):
        raise PublicZoneMoveError("Graveyard exile follow-up requires the current public face view")
    followup.validate_move(spec)
    if followup.schema_version in {2, 3}:
        return tuple(row for row in host.public_zone_move_object_rows(actor) if row.zone == "battlefield")
    if getattr(host.state, "characteristic_query_boundary_version", None) != PUBLIC_ZONE_FRONT_FACE_VERSION:
        raise PublicZoneMoveError("Graveyard exile follow-up requires the current public face view")
    return tuple(
        row for row in host.public_zone_move_object_rows(actor)
        if row.zone == "graveyard"
        and getattr(host.state.cards.get(row.object_id), "is_card_object", False)
    )


def _committed_exile_count(snapshot, rows, moved, followup) -> int:
    selected = {value.object_id: value.logical_object_id for value in snapshot.objects}
    counted = {row.object_id for row in query_objects(rows, followup.query)} & selected.keys()
    return len({
        card.object_id for card in moved
        if card.object_id in counted and card.logical_object_id != selected[card.object_id]
        and (card.zone != "battlefield" if followup.schema_version == 2 else card.zone == "exile")
    })


def _same_turn_graveyard_return_rows(host, actor, spec):
    from .public_history_query_model import matching_public_history_events

    refs = spec.target_refs
    if spec.schema_version == TARGETED_SAME_TURN_GRAVEYARD_RETURN_VERSION:
        if not isinstance(refs, tuple):
            raise PublicZoneMoveError("Targeted arrival return must use the revalidated target set")
        if not refs:
            return ()
    events = matching_public_history_events(host.state, actor, same_turn_graveyard_return_history_query(spec.seat_relation))
    if events is None:
        raise PublicZoneMoveError("Current battlefield-to-graveyard arrival history is unavailable")
    arrivals = {(event.actor, event.object_incarnation) for event in events}
    return tuple(
        row for row in host.public_zone_move_object_rows(actor)
        if row.zone == "graveyard" and (row.owner, row.logical_object_id) in arrivals
        and (refs is None or row.ref in refs)
    )


def resolve_fixed_mixed_zone_exile(host, *, actor, spec, reason, replacement_selections=()):
    """Select the entire fixed hidden and public batch inside the authoritative owner."""
    if spec.scope is MixedZoneExileScope.CONTROLLER_HAND_DRAW_AND_PLAY:
        from .rules.controller_hand_exile import resolve_controller_hand_exile

        return resolve_controller_hand_exile(host, actor, reason, replacement_selections)
    from .zone_transitions import ZoneTransitionOwner

    public_types = frozenset({"artifact", "creature", "land"})
    live = frozenset(host.active_seats)
    affected = set()
    for seat in live:
        for zone in ("hand", "graveyard"):
            affected.update(object_id for object_id in host.state.players[seat].zones[zone]
                            if host.state.cards[object_id].is_card_object)
    if spec.scope is MixedZoneExileScope.ARTIFACTS_CREATURES_LANDS_HANDS_AND_GRAVEYARDS:
        for card in host.state.cards.values():
            if card.zone != "battlefield" or card.phased_out:
                continue
            types, _, _ = host._type_parts(str(host._effective_card_data(card).get("type_line") or ""))
            if not public_types.isdisjoint(types):
                affected.add(card.object_id)
    changes = tuple((object_id, "exile") for object_id in sorted(affected))
    owners = tuple(sorted({host.state.cards[object_id].owner for object_id in affected}))
    if changes or replacement_selections:
        ZoneTransitionOwner(host).move_cards_simultaneously(
            changes, reason=reason, log=False, replacement_selections=replacement_selections,
        )
    host._log(actor, "effect.fixed_mixed_zone_exile", "Moved the fixed mixed-zone batch toward exile.",
              {"scope": spec.scope.value, "affected_count": len(changes)}, importance=2,
              changed_players=owners,
              changed_objects=tuple(object_id for object_id, _ in changes
                                    if host.state.cards[object_id].zone == "exile"
                                    and not host.state.cards[object_id].face_down))
    return len(changes)


def resolve_public_zone_move_set(
    host: PublicZoneMoveHost,
    *,
    actor: str,
    spec: PublicZoneMoveSetSpec | FixedMixedZoneExileSpec,
    reason: str,
    source_ref: str | None = None,
    replacement_selections: Sequence[str | Mapping[str, Any]] = (),
    followup: PublicExileFollowup | None = None,
    aura_target_refs: Mapping[str, str] | None = None,
) -> PublicZoneMoveSnapshot | CommittedQuantityFollowup | int:
    """Resolve a fixed public set through the canonical simultaneous owner."""

    _nonempty(actor, field="Public zone-move actor")
    _nonempty(reason, field="Public zone-move reason")
    if isinstance(spec, FixedMixedZoneExileSpec):
        if followup is not None or aura_target_refs is not None:
            raise PublicZoneMoveError("Mixed-zone exile permits one move without a follow-up or Aura choices")
        return resolve_fixed_mixed_zone_exile(
            host, actor=actor, spec=spec, reason=reason, replacement_selections=replacement_selections,
        )
    if aura_target_refs is not None and (
        spec.schema_version not in PUBLIC_AURA_ENTRY_VERSIONS or spec.destination is not PublicZoneDestination.BATTLEFIELD
    ):
        raise PublicZoneMoveError("Aura entry selections require version-three battlefield entry")
    rows = _exile_followup_rows(host, actor, spec, followup) if followup is not None else None
    if spec.schema_version in SAME_TURN_GRAVEYARD_RETURN_VERSIONS:
        rows = _same_turn_graveyard_return_rows(host, actor, spec)
    snapshot = snapshot_public_zone_move_set(
        host, actor=actor, spec=spec, source_ref=source_ref, object_rows=rows,
    )
    for value in snapshot.objects:
        card = host.state.cards.get(value.object_id)
        if (
            card is None
            or card.logical_object_id != value.logical_object_id
            or card.ref != value.ref
            or card.owner != value.owner
            or card.controller != value.controller
            or card.zone != spec.origin.value
            or card.phased_out
            or (
                spec.origin is PublicZoneOrigin.GRAVEYARD
                and not bool(getattr(card, "is_card_object", True))
            )
        ):
            raise PublicZoneMoveError(
                "Public zone-move snapshot became stale before commit"
            )
    moved = ()
    if snapshot.objects:
        from .zone_transitions import ZoneTransitionOwner

        controllers = (
            {
                value.object_id: (
                    actor
                    if spec.battlefield_controller
                    is PublicBattlefieldController.ACTOR
                    else value.owner
                )
                for value in snapshot.objects
            }
            if spec.destination is PublicZoneDestination.BATTLEFIELD
            else None
        )
        front_faces = (graveyard_front_faces(host, snapshot.objects)
                       if spec.schema_version == PUBLIC_CHOSEN_DIFFERENT_NAME_RETURN_VERSION else {})
        moved = ZoneTransitionOwner(host).move_cards_simultaneously(
            tuple(
                (value.object_id, spec.destination.value)
                for value in snapshot.objects
            ),
            reason=reason,
            log=False,
            tapped=(
                spec.battlefield_tapped
                if spec.destination is PublicZoneDestination.BATTLEFIELD
                else None
            ),
            destination_controllers=controllers,
            putting_players={object_id: actor for object_id in controllers} if controllers is not None else None,
            **({"aura_target_refs": aura_target_refs} if aura_target_refs is not None else {}),
            **({"entry_faces": front_faces} if front_faces else {}),
            replacement_selections=replacement_selections,
        )
    host._log(
        actor,
        "effect.public_zone_move_set",
        f"Moved {len(snapshot.objects)} public object(s) toward {spec.destination.value}.",
        {
            "snapshot_fingerprint": snapshot.fingerprint,
            "affected_count": len(snapshot.objects),
            "origin": spec.origin.value,
            "requested_destination": spec.destination.value,
            "reason": reason,
        },
        importance=2,
        changed_objects=tuple(value.object_id for value in snapshot.objects),
        changed_players=tuple(
            sorted({value.owner for value in snapshot.objects})
        ),
    )
    if followup is not None:
        if followup.schema_version == 3:
            from .rules.affected_library_search_batch import committed_exile_land_search

            original = {obj.object_id: obj for obj in snapshot.objects}
            committed = tuple(card.object_id for card in moved
                              if card.zone == "exile" and card.object_id in original
                              and card.logical_object_id != original[card.object_id].logical_object_id)
            return committed_exile_land_search(host, actor, (original[oid].controller for oid in committed))
        return followup.from_committed_count(
            _committed_exile_count(snapshot, rows, moved, followup), target_player=spec.target_seat,
        )
    return snapshot


def exile_public_graveyard_card(
    host: PublicZoneMoveHost,
    object_ref: str,
    *,
    actor: str,
    reason: str,
    replacement_selections: Sequence[str | Mapping[str, Any]] = (),
) -> str:
    """Move one revalidated physical card from a public graveyard to exile."""

    return exile_public_graveyard_card_transition(
        host, object_ref, actor=actor, reason=reason, replacement_selections=replacement_selections,
    ).object_ref


def exile_public_graveyard_card_transition(
    host: PublicZoneMoveHost,
    object_ref: str,
    *,
    actor: str,
    reason: str,
    replacement_selections: Sequence[str | Mapping[str, Any]] = (),
) -> SingleObjectZoneTransitionResult:
    """Commit the existing public graveyard exile and return its actual movement receipt."""

    card = host._resolve_object(actor, object_ref, zones={"graveyard"})
    if not bool(getattr(card, "is_card_object", True)):
        raise PublicZoneMoveError(
            "Public graveyard exile requires a physical card"
        )
    try:
        plan = prepare_single_object_zone_transition(
            host,
            request_for_card(card),
            actor=actor,
            reason=reason,
            requested_destination=SingleObjectDestination.EXILE,
            expected_origin=SingleObjectOrigin.GRAVEYARD,
            replacement_selections=replacement_selections,
        )
        result = commit_prevalidated_single_object_zone_transition(host, plan)
    except SingleObjectZoneTransitionError as exc:
        raise PublicZoneMoveError(str(exc)) from exc
    host._log(
        actor,
        "card.exile_from_graveyard",
        f"{result.object_ref} moved from a graveyard toward exile.",
        {
            "object": result.object_ref,
            "owner": result.owner,
            "origin": result.origin.value,
            "requested_destination": SingleObjectDestination.EXILE.value,
            "destination": result.actual_destination,
            "reason": reason,
        },
        importance=2,
        changed_objects=(result.object_id,),
        changed_players=(result.owner,),
    )
    return result


__all__ = [
    "PUBLIC_AURA_ENTRY_VERSION",
    "PUBLIC_GRAVEYARD_POWER_RETURN_CAPABILITY",
    "PUBLIC_GRAVEYARD_POWER_RETURN_VERSION",
    "public_graveyard_power_return_spec",
    "exile_public_graveyard_card",
    "exile_public_graveyard_card_transition",
    "FIXED_OWNER_ZONE_MOVE_CAPABILITY",
    "FIXED_OWNER_ZONE_MOVE_MECHANIC",
    "PublicZoneDestination",
    "PublicZoneMoveError",
    "PublicZoneMoveHost",
    "PublicZoneMoveObject",
    "PublicZoneMoveQuery",
    "PublicZoneMoveSetSpec",
    "PublicZoneMoveSnapshot",
    "PublicZoneOrigin",
    "PublicZoneRelationAxis",
    "PublicZoneSeatRelation",
    "resolve_public_zone_move_set",
    "select_public_zone_move_objects",
    "snapshot_public_zone_move_set",
]
