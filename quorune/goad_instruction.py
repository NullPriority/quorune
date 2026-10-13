from __future__ import annotations

"""Closed Goad requests resolved through the canonical designation owner."""

from typing import Mapping
from dataclasses import replace
from .object_predicate import ObjectQuerySpec
from .compiler.fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed

GOAD_MECHANIC = 'fixed-goad'
GOAD_CAPABILITY = 'combat.goad.fixed_instruction'


def validate_fixed_goad(effect):
    if not isinstance(effect,Mapping) or effect.get('op')!='goad' or type(effect.get('schema_version')) is not int or effect['schema_version']!=2:
        raise ValueError('Fixed Goad requires its explicit schema version')
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
    if query.controller is not None and query.controller not in host.active_seats:
        raise ValueError('Goad query has an unavailable controller binding')
    if any(seat not in host.active_seats for seat in query.excluded_controllers):
        raise ValueError('Goad query has an unavailable excluded controller')
    from .object_query import query_objects
    rows=host.affected_permanent_object_rows(actor)
    return tuple(row.ref for row in sorted(query_objects(rows,query),key=lambda row:row.logical_object_id))
