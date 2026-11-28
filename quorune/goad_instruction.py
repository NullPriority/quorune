from __future__ import annotations

"""Closed Goad requests resolved through the canonical designation owner."""

from typing import Mapping
from dataclasses import replace
from .object_predicate import ObjectQuerySpec
from .compiler.fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed

GOAD_MECHANIC = 'fixed-goad'
GOAD_CAPABILITY = 'combat.goad.fixed_instruction'
GOAD_BLOCK_PROHIBITION_VERSION = 3

_BLOCK_PROHIBITION = {"restriction": "cant_block", "duration": "until_next_turn"}


def goad_block_prohibition_effect():
    query = ObjectQuerySpec(
        zones=("battlefield",), types_all=("creature",), excluded_controllers=("$controller",),
    )
    return {
        "op": "goad", "schema_version": GOAD_BLOCK_PROHIBITION_VERSION, "predicate": query.to_dict(),
        "following_declaration_rule": dict(_BLOCK_PROHIBITION),
    }


def _validate_goad_block_prohibition(effect):
    from .replacement.immutable import thaw_value

    expected = goad_block_prohibition_effect()
    if set(effect) != set(expected) or not isinstance(effect["predicate"], Mapping):
        raise ValueError("Goad with a block prohibition has malformed fields")
    query = ObjectQuerySpec.from_dict(effect["predicate"])
    if query.excluded_controllers:
        expected["predicate"]["excluded_controllers"] = list(query.excluded_controllers)
    if (len(query.excluded_controllers) != 1 or not query.excluded_controllers[0]
            or thaw_value(effect) != expected):
        raise ValueError("Goad with a block prohibition requires one exact opponent-creature snapshot")
    return query


def validate_fixed_goad(effect):
    if not isinstance(effect,Mapping) or effect.get('op')!='goad' or type(effect.get('schema_version')) is not int or effect['schema_version'] not in {2, GOAD_BLOCK_PROHIBITION_VERSION}:
        raise ValueError('Fixed Goad requires its explicit schema version')
    if effect["schema_version"] == GOAD_BLOCK_PROHIBITION_VERSION:
        return _validate_goad_block_prohibition(effect)
    if set(effect)=={'op','schema_version','card'}:
        if type(effect['card']) is not str or not effect['card']:raise ValueError('Goad requires one bound creature')
        return None
    if set(effect)!={'op','schema_version','predicate'}:raise ValueError('Goad instruction has unknown or missing fields')
    query=ObjectQuerySpec.from_dict(effect['predicate'])
    normalized=replace(query,controller='$controller' if query.controller is not None else None,
        excluded_controllers=('$controller',) if query.excluded_controllers else (),
        exclude_ref='$source' if query.exclude_ref is not None else None)
    if 'creature' not in query.types_all or not fixed_resolution_characteristic_query_is_closed(normalized,target_schema=None):
        raise ValueError('Goad set requires a closed public creature query')
    if query.controller is not None and type(query.controller) is not str or any(type(v) is not str for v in query.excluded_controllers):
        raise ValueError('Goad controller bindings must be strings')
    return query


def resolve_fixed_goad_refs(host,effect,*,actor):
    query=validate_fixed_goad(effect)
    if query is None:return (effect['card'],)
    if (effect["schema_version"] == GOAD_BLOCK_PROHIBITION_VERSION
            and query.excluded_controllers != (actor,)):
        raise ValueError("The opponent creature set requires the original resolving controller")
    if query.controller is not None and query.controller not in host.active_seats:
        raise ValueError('Goad query has an unavailable controller binding')
    if any(seat not in host.active_seats for seat in query.excluded_controllers):
        raise ValueError('Goad query has an unavailable excluded controller')
    from .object_query import query_objects
    rows=host.affected_permanent_object_rows(actor)
    return tuple(row.ref for row in sorted(query_objects(rows,query),key=lambda row:row.logical_object_id))


WATCHED_COMBAT_DRAW_FIELD = "watched_combat_draw_version"
WATCHED_COMBAT_DRAW_CAPABILITY = "trigger.delayed.goaded_creature_combat_draw"
WATCHED_COMBAT_DRAW_MECHANIC = "goaded-creature-combat-draw"
HAND_COUNT_UNBLOCKABLE_FIELD = "hand_count_unblockable_version"
HAND_COUNT_UNBLOCKABLE_CAPABILITY = "resolution.effect.hand_count_bonus_and_unblockable"
HAND_COUNT_UNBLOCKABLE_MECHANIC = "hand-count-bonus-and-unblockable"


def watched_combat_draw_schema():
    from .target_cardinality import AnnouncedTargetCardinality

    return {
        "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
        "controller_relation": "opponent", "distinct_controllers": True, "retain_controller": True,
        "count_parameter": AnnouncedTargetCardinality(
            parameter="any_number", selection="up_to", schema_version=2,
        ).to_dict(),
    }


def watched_combat_draw_effect():
    return {"op": "goad", "cards": "$targets", WATCHED_COMBAT_DRAW_FIELD: 1}


def hand_count_unblockable_schema():
    return {"zones": ["battlefield"], "categories": ["permanent"],
            "count": 1, "types_any": ["creature"], "controller_relation": "you"}


def hand_count_unblockable_effect():
    return {"op": "grant_declaration_restriction_until_end_of_turn", "card": "$target.0",
            "restriction": "unblockable", HAND_COUNT_UNBLOCKABLE_FIELD: 1}
