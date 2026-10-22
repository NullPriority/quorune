from __future__ import annotations

from ..creature_subtypes import canonical_creature_subtype_surface
from ..object_predicate import ObjectQuerySpec


_COLORS = {'white': 'W', 'blue': 'U', 'black': 'B', 'red': 'R', 'green': 'G'}
_LANDS = {'plains': 'plains', 'islands': 'island', 'swamps': 'swamp',
          'mountains': 'mountain', 'forests': 'forest'}


def _leaf(text: str, *, creatures: bool = False) -> ObjectQuerySpec | None:
    other = text.startswith('other ')
    word = text.removeprefix('other ')
    options = {'zones': ('battlefield',), 'exclude_ref': '$source' if other else None}
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
