from __future__ import annotations

from dataclasses import replace
import re

from ..creature_subtypes import canonical_creature_subtype_surface
from ..object_predicate import ObjectQuerySpec


_COLORS = {'white': 'W', 'blue': 'U', 'black': 'B', 'red': 'R', 'green': 'G'}
_LANDS = {'plains': 'plains', 'islands': 'island', 'swamps': 'swamp',
          'mountains': 'mountain', 'forests': 'forest'}


def _leaf(text: str, *, creatures: bool = False) -> ObjectQuerySpec | None:
    other = text.startswith('other ')
    word = text.removeprefix('other ')
    options = {'zones': ('battlefield',), 'exclude_ref': '$source' if other else None}
    if word in {'land', 'lands'} and not creatures:
        return ObjectQuerySpec(**options, types_all=('land',))
    if word in _COLORS and creatures:
        return ObjectQuerySpec(**options, types_all=('creature',), colors_all=(_COLORS[word],))
    if word == 'snow' and creatures:
        return ObjectQuerySpec(**options, types_all=('creature',), supertypes_all=('snow',))
    if word in _LANDS and not creatures:
        return ObjectQuerySpec(**options, types_all=('land',), subtypes_all=(_LANDS[word],))
    subtype = canonical_creature_subtype_surface(word)
    if subtype is None:
        return None
    return ObjectQuerySpec(**options, types_all=('creature',) if creatures else (), subtypes_all=(subtype,))


def fixed_receiver_union_subject(subject: str) -> tuple[str, ObjectQuerySpec, bool] | None:
    """Parse a flat union with shared or explicitly repeated controller scope."""
    text = ' '.join(subject.casefold().split())
    text = text.removeprefix('each ')
    if ' and ' not in text or ', ' in text or ' or ' in text:
        return None
    global_other = text.startswith('other ')
    text = text.removeprefix('other ')
    relation = 'any'
    if ' you control and ' in text:
        parts = text.split(' and ')
        if not all(part.endswith(' you control') for part in parts):
            return None
        relation = 'source_controller'
        leaves = [_leaf(part.removesuffix(' you control')) for part in parts]
    else:
        if text.endswith(' you control'):
            relation = 'source_controller'
            text = text.removesuffix(' you control')
        parts = text.split(' and ')
        if all(part.endswith(' creatures') for part in parts):
            leaves = [_leaf(part.removesuffix(' creatures'), creatures=True) for part in parts]
        elif text.endswith(' creatures'):
            parts = text.removesuffix(' creatures').split(' and ')
            leaves = [_leaf(part, creatures=True) for part in parts]
        else:
            leaves = [_leaf(part) for part in parts]
    if not 2 <= len(leaves) <= 4 or any(part is None for part in leaves):
        return None
    if len(set(leaves)) != len(leaves):
        return None
    return relation, ObjectQuerySpec(zones=('battlefield',), any_of=tuple(leaves)), global_other


def characteristic_quantity_union(text: str, *, zone: str) -> ObjectQuerySpec | None:
    """Count one flat union of current public types and subtypes."""
    if zone != 'battlefield' or not re.search(r'\band(?:/or)?\b', text):
        return None
    tapped = None
    if text.startswith(('tapped ', 'untapped ')):
        state, text = text.split(' ', 1)
        tapped = state == 'tapped'
    parts = re.split(r',\s*(?:and(?:/or)?\s+)?|\s+and(?:/or)?\s+', text)
    if not 2 <= len(parts) <= 4 or not all(parts):
        return None
    card_types = {kind + 's': kind for kind in (
        'artifact', 'battle', 'creature', 'enchantment', 'land', 'planeswalker')}
    card_types.update({kind: kind for kind in card_types.values()})
    leaves = []
    for part in parts:
        if part in card_types:
            leaf = ObjectQuerySpec(zones=(zone,), types_all=(card_types[part],))
        elif part in {'equipment', 'vehicle', 'vehicles'}:
            leaf = ObjectQuerySpec(zones=(zone,), types_all=('artifact',),
                subtypes_all=('vehicle' if part == 'vehicles' else part,))
        else:
            leaf = _leaf(part)
        if leaf is None:
            return None
        leaves.append(leaf)
    if len(set(leaves)) != len(leaves):
        return None
    if all(part in card_types for part in parts):
        return ObjectQuerySpec(zones=(zone,),
            types_any=tuple(card_types[part] for part in parts), tapped=tapped)
    if tapped is not None:
        return None
    return ObjectQuerySpec(zones=(zone,), any_of=tuple(leaves))


def relative_characteristic_quantity_query(text: str, *, compile_descriptor):
    """Keep a relative creature restriction on every union branch."""
    match = re.fullmatch(
        r'(?P<kind>permanents|creatures) you control that are (?P<body>.+)', text, re.I)
    if match is None:
        return None
    parsed = compile_descriptor(match['body'])
    if parsed is None or parsed[1]:
        return None
    query = parsed[0]
    if match['kind'].casefold() == 'creatures':
        def creature_leaf(leaf):
            return replace(leaf, types_all=tuple(dict.fromkeys((*leaf.types_all, 'creature'))))
        query = (replace(query, any_of=tuple(creature_leaf(leaf) for leaf in query.any_of))
            if query.any_of else creature_leaf(query))
    return query
