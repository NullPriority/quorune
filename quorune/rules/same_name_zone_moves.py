from __future__ import annotations

"""Current rules-name membership for one targeted public zone move."""

from collections.abc import Mapping
from enum import Enum

from ..object_predicate import ObjectQuerySpec
from ..public_zone_moves import (
    PUBLIC_SAME_NAME_MOVE_TARGET_FIELD,
    PUBLIC_SAME_NAME_MOVE_VERSION,
    PublicZoneDestination,
    PublicZoneMoveError,
    PublicZoneMoveSetSpec,
    PublicZoneOrigin,
    PublicZoneRelationAxis,
    PublicZoneSeatRelation,
)
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import contains_nested_key


SAME_NAME_ZONE_MOVE_CAPABILITY = "zone.move.same_name_public_set"
_TARGET = "$target.0"
_GRAVEYARD_CARDS = ObjectQuerySpec(zones=("graveyard",), token=False)
_BATTLEFIELD_CREATURES = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))
_MECHANICS = {"cr-115-targets", "fixed-public-zone-move", "fixed-public-zone-move-set"}


class SameNameZoneMoveScope(str, Enum):
    EXILE_BATTLEFIELD_CREATURES = "exile_battlefield_creatures"
    RETURN_OWN_GRAVEYARD_CARDS = "return_own_graveyard_cards"
    RETURN_BATTLEFIELD_PERMANENTS = "return_battlefield_permanents"

    def target_schema(self):
        if self is self.RETURN_OWN_GRAVEYARD_CARDS:
            from .graveyard_card_targets import PublicGraveyardCardTargetKind, PublicGraveyardCardTargetSpec

            return PublicGraveyardCardTargetSpec(PublicGraveyardCardTargetKind.CREATURE_CARD, owner_relation="you").to_target_schema()
        from ..compiler.direct_target import DirectPermanentTargetSpec

        if self is self.RETURN_BATTLEFIELD_PERMANENTS:
            return DirectPermanentTargetSpec(types_none=("land",)).to_target_schema()
        return DirectPermanentTargetSpec(types_any=("creature",)).to_target_schema()

    def permits_anchor(self, row):
        if self is self.RETURN_BATTLEFIELD_PERMANENTS:
            return "land" not in row.types
        return "creature" in row.types

    def spec(self):
        return PublicZoneMoveSetSpec(
            **_selection_policy(self), schema_version=PUBLIC_SAME_NAME_MOVE_VERSION, same_name_target=_TARGET,
        )

    @property
    def result_mechanic(self):
        return "exile" if self is self.EXILE_BATTLEFIELD_CREATURES else "return-to-owner-hand"


def _selection_policy(scope):
    if scope is SameNameZoneMoveScope.EXILE_BATTLEFIELD_CREATURES:
        return {
            "origin": PublicZoneOrigin.BATTLEFIELD,
            "destination": PublicZoneDestination.EXILE,
            "relation_axis": PublicZoneRelationAxis.CONTROLLER,
            "seat_relation": PublicZoneSeatRelation.ANY,
            "query": _BATTLEFIELD_CREATURES,
        }
    if scope is SameNameZoneMoveScope.RETURN_BATTLEFIELD_PERMANENTS:
        return {
            "origin": PublicZoneOrigin.BATTLEFIELD,
            "destination": PublicZoneDestination.OWNER_HAND,
            "relation_axis": PublicZoneRelationAxis.CONTROLLER,
            "seat_relation": PublicZoneSeatRelation.ANY,
            "query": ObjectQuerySpec(zones=("battlefield",)),
        }
    return {
        "origin": PublicZoneOrigin.GRAVEYARD,
        "destination": PublicZoneDestination.OWNER_HAND,
        "relation_axis": PublicZoneRelationAxis.OWNER,
        "seat_relation": PublicZoneSeatRelation.ACTOR,
        "query": _GRAVEYARD_CARDS,
    }


def same_name_zone_move_scope(spec):
    ref = spec.same_name_target
    if (spec.schema_version != PUBLIC_SAME_NAME_MOVE_VERSION or type(ref) is not str
            or not ref or ref != ref.strip() or spec.exclude_source):
        raise PublicZoneMoveError("Same-name zone moves require an exact versioned target reference")
    for scope in SameNameZoneMoveScope:
        if all(getattr(spec, field) == value for field, value in _selection_policy(scope).items()):
            return scope
    raise PublicZoneMoveError("Same-name zone move domain or destination is unsupported")


def _same_name_effect_scope(effect, target_schema):
    if set(effect) != {"op", "source", "set"} or effect["op"] != "move_public_zone_set" or effect["source"] != "$source":
        raise PublicZoneMoveError("Same-name zone moves require one closed public move instruction")
    spec = PublicZoneMoveSetSpec.from_dict(effect["set"])
    scope = same_name_zone_move_scope(spec)
    if spec.same_name_target != _TARGET or thaw_value(target_schema) != scope.target_schema():
        raise PublicZoneMoveError("Same-name zone moves require their exact ordinary target anchor")
    return scope


def _dependencies():
    return (
        SAME_NAME_ZONE_MOVE_CAPABILITY,
        "target.revalidate_resolution",
        "zone.move.fixed_public_set",
    )


def same_name_zone_move_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or not _MECHANICS.issubset(mechanic_ids):
        return ()
    try:
        scope = _same_name_effect_scope(effects[0], target_schema)
    except (KeyError, TypeError, ValueError):
        return ()
    return _dependencies() if scope.result_mechanic in mechanic_ids else ()


def same_name_zone_move_runtime_dependencies(program):
    if not contains_nested_key((program.effects, program.handlers), PUBLIC_SAME_NAME_MOVE_TARGET_FIELD):
        return ()
    if (program.event != "resolve" or not program.ability_id.startswith("spell:") or len(program.effects) != 1
            or contains_nested_key(program.handlers, PUBLIC_SAME_NAME_MOVE_TARGET_FIELD)):
        raise PublicZoneMoveError("Same-name zone moves require their single resolving spell frame")
    _same_name_effect_scope(program.effects[0], program.target_schema)
    return _dependencies()


def select_same_name_zone_rows(query, spec, rows):
    """Use the current anchor's public rules names before any recipient moves."""
    anchor = next((row for row in rows if row.ref == spec.same_name_target), None)
    if anchor is None or not same_name_zone_move_scope(spec).permits_anchor(anchor):
        raise PublicZoneMoveError("Same-name zone moves require their current legal target anchor")
    names = set(query.public_zone_move_rules_names(anchor))
    if not names:
        return (anchor,)
    return tuple(row for row in rows if row is anchor or not names.isdisjoint(query.public_zone_move_rules_names(row)))
