from __future__ import annotations

"""Complete source-backed result views for fixed entry search and exile."""

from collections.abc import Mapping
from ..library_search_model import FixedCountedLibrarySearchTemplate
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from ..rules.capabilities import capability_dependencies_for_node
from ..rules.public_zone_move_capability_shapes import public_graveyard_card_exile_node_capabilities
from ..targets import target_plan


def _graveyard_card_target(schema):
    required = {'zones', 'categories', 'count'}
    if (not isinstance(schema, Mapping) or not required <= set(schema)
            or set(schema)-required-{'owner_relation'} or schema['zones'] != ['graveyard']
            or schema['categories'] != ['card'] or type(schema['count']) is not int
            or schema['count'] != 1 or schema.get('owner_relation', 'any') != 'any'):return None
    try:return target_plan({**schema, 'owner_relation': 'any'})
    except (TypeError, ValueError):return None


def fixed_graveyard_exile_views_equal(current, old):
    a, b = (_graveyard_card_target(p.target_schema) for p in (current, old))
    if a is None or b is None or a != b:return False
    if (list(old.effects) != [{'op': 'exile', 'card': '$target.0'}]
            or list(current.effects) != [{'op': 'exile_public_graveyard_card', 'card': '$target.0'}]):return False
    required = public_graveyard_card_exile_node_capabilities(effects=current.effects,
        target_schema=current.target_schema, mechanic_ids=current.coverage)
    return bool(required) and set(required) <= set(current.capability_dependencies)


def _optional_graveyard_search_views_equal(current, old):
    if current.target_schema is not None or old.target_schema is not None:return False
    if len(current.effects) != 1 or len(old.effects) != 1:return False
    outer, previous = current.effects[0], old.effects[0]
    if (set(outer) != {'op', 'player', 'effects'} or outer['op'] != 'offer_optional_effect'
            or outer['player'] != '$controller' or not isinstance(outer['effects'], (list, tuple))
            or len(outer['effects']) != 1):return False
    fields = {'op', 'searching_player', 'zone', 'selector', 'count', 'destination',
        'reveal', 'shuffle_after', 'optional'}
    if (set(previous) != fields or previous['op'] != 'search' or previous['zone'] != 'library'
            or previous['searching_player'] != '$controller' or previous['destination'] != 'graveyard'
            or previous['reveal'] is not False or previous['shuffle_after'] is not True
            or previous['optional'] is not True):return False
    count = previous['count'];selector = previous['selector']
    if (not isinstance(count, Mapping) or set(count) != {'minimum', 'maximum'}
            or any(type(count[k]) is not int for k in count)
            or dict(count) != {'minimum': 0, 'maximum': 1}
            or not isinstance(selector, Mapping) or set(selector) != {'types'}
            or not isinstance(selector['types'], (list, tuple))):return False
    try:
        query = ObjectQuerySpec(zones=('library',), types_all=tuple(selector['types']))
        if query != ObjectQuerySpec(zones=('library',), types_all=('artifact',)):return False
        actual = FixedCountedLibrarySearchTemplate.from_effect(outer['effects'][0])
        expected = FixedCountedLibrarySearchTemplate(count=1, optional_count=False,
            selector=FrozenMap({'types': ['artifact']}), destination='graveyard', reveal=False)
    except (TypeError, ValueError, KeyError):return False
    required = capability_dependencies_for_node(effects=current.effects,
        target_schema=None, mechanic_ids=current.coverage)
    return actual == expected and bool(required) and set(required) <= set(current.capability_dependencies)


def fixed_trigger_results_equal(current, old):
    return (fixed_graveyard_exile_views_equal(current, old)
        or _optional_graveyard_search_views_equal(current, old))
