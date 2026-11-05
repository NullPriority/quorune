"""Closed subtype quantity nouns from the existing pinned type vocabulary."""
from ..type_subtype_vocabulary import CARD_TYPE_SUBTYPES


def _plural(subtype):
    if subtype.endswith("'s") or subtype in {'equipment', 'plains'}:
        return subtype
    if subtype.endswith('y') and subtype[-2:] not in {'ay', 'ey', 'oy', 'uy'}:
        return subtype[:-1]+'ies'
    return subtype+('es' if subtype.endswith(('s', 'x', 'ch', 'sh')) else 's')


def noncreature_quantity_subtype_fields(descriptor, *, zone, definition_extensions):
    """Correlate a complete subtype noun with its actual card-type domain."""
    value = descriptor.casefold().replace('’', "'")
    matches = {(kind, subtype) for kind, subtypes in CARD_TYPE_SUBTYPES.items()
        if kind not in {'creature', 'kindred'} for subtype in subtypes
        if value in {subtype, _plural(subtype)}}
    if not matches:
        return None
    subtypes = {subtype for kind, subtype in matches}
    if len(subtypes) != 1:
        return None
    kinds = tuple(sorted(kind for kind, subtype in matches))
    spell_types = set(kinds) <= {'instant', 'sorcery'}
    if spell_types and (zone != 'graveyard' or not definition_extensions):
        return None
    fields = {'types_all' if len(kinds)==1 else 'types_any': kinds,
        'subtypes_all': (next(iter(subtypes)),)}
    if spell_types:
        fields['token'] = False
    return fields
