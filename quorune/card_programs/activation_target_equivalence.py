from __future__ import annotations

"""Comparison-only aliases for one complete battlefield creature target."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from ..targets import target_plan


def _single_creature_target(schema):
    required = {'zones', 'categories', 'count'}
    aliases = {'creature', 'types_any', 'controller', 'controller_relation'}
    if (not isinstance(schema, Mapping) or not required <= set(schema)
            or set(schema)-required-aliases or schema['zones'] != ['battlefield']
            or schema['categories'] != ['permanent'] or type(schema['count']) is not int
            or schema['count'] != 1 or ('controller' in schema and 'controller_relation' in schema)):
        return None
    value = deepcopy(dict(schema))
    if 'creature' in value:
        if value.pop('creature') is not True or 'types_any' in value:
            return None
        value['types_any'] = ['creature']
    if value.get('types_any') != ['creature']:
        return None
    if 'controller' in value:
        value['controller_relation'] = value.pop('controller')
    try:
        return target_plan(value)
    except (TypeError, ValueError):
        return None


def reviewed_activation_with_equivalent_target(current, old):
    if current.target_schema == old.target_schema:
        return old
    a, b = (_single_creature_target(p.target_schema) for p in (current, old))
    if a is None or b is None or a != b or len(current.handlers) != 1 or len(old.handlers) != 1:
        return None
    for p in (current, old):
        descriptor = p.handlers[0]
        if (descriptor.get('handler_id') != 'activation.catalog.pinned.v1'
                or not isinstance(descriptor.get('ability'), Mapping)
                or descriptor['ability'].get('target_schema') != p.target_schema):
            return None
    handlers = deepcopy(old.handlers)
    handlers[0]['ability']['target_schema'] = deepcopy(current.target_schema)
    return replace(old, target_schema=deepcopy(current.target_schema), handlers=handlers)
