from __future__ import annotations

"""Strict v2 selection/result capability binding beside the existing owner."""

from typing import Any, Mapping

from ..object_predicate import ObjectQuerySpec
from ..resolution_characteristic_model import (
    PERMANENT_CHARACTERISTIC_CAPABILITY, PERMANENT_CHARACTERISTIC_MECHANIC,
    fixed_resolution_characteristic_instruction,
)
from ..compiler.fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed


def fixed_resolution_characteristic_set_is_closed(query: ObjectQuerySpec) -> bool:
    from dataclasses import replace
    symbolic=replace(query,controller='$controller' if query.controller is not None else None,
                     excluded_controllers=('$controller',) if query.excluded_controllers else ())
    return not query.keywords_none and fixed_resolution_characteristic_query_is_closed(symbolic,target_schema=None)


def fixed_resolution_characteristic_capabilities(*, effects: Any, target_schema: Any, mechanic_ids: Any) -> tuple[str, ...]:
    mechanics=set(mechanic_ids)
    if PERMANENT_CHARACTERISTIC_MECHANIC not in mechanics or 'cr-611-continuous-effects' not in mechanics or len(effects)!=1:return ()
    try:spec,selection=fixed_resolution_characteristic_instruction(effects[0])
    except (TypeError,ValueError,KeyError):return ()
    if not {keyword.casefold() for keyword in spec.keywords}<=mechanics:return ()
    dependencies={PERMANENT_CHARACTERISTIC_CAPABILITY,'continuous.resolution.fixed_characteristics_until_end_of_turn'}
    if isinstance(selection,ObjectQuerySpec):
        if selection.keywords_none or not fixed_resolution_characteristic_query_is_closed(selection,target_schema=target_schema):return ()
        if spec.card_types is None and 'creature' not in selection.types_all:return ()
    elif selection=='$source.zone_object':
        if target_schema is not None:return ()
    elif selection=='$target.0':
        from .node_capability_shapes import direct_target_predicate_capabilities
        if not isinstance(target_schema,Mapping) or target_schema.get('count')!=1 or target_schema.get('zones')!=['battlefield'] or target_schema.get('categories')!=['permanent']:return ()
        if spec.card_types is None and target_schema.get('types_any')!=['creature']:return ()
        dependencies.update(direct_target_predicate_capabilities(target_schema))
    else:return ()
    if target_schema is not None:
        if 'cr-115-targets' not in mechanics:return ()
        dependencies.add('target.revalidate_resolution')
    return tuple(sorted(dependencies))
