"""Public subtype queries for existing fixed owner-hand return prices."""
from ..basic_land_types import BASIC_LAND_MANA
from ..creature_subtypes import canonical_creature_subtype
from ..object_predicate import ObjectQuerySpec


def qualified_return_cost_query(quality: str) -> ObjectQuerySpec | None:
    if type(quality) is not str:
        return None
    normalized = ' '.join(quality.casefold().split())
    subtype = normalized if normalized in BASIC_LAND_MANA else canonical_creature_subtype(normalized)
    if subtype is None:
        return None
    # A creature subtype can also belong to a noncreature Kindred permanent.
    return ObjectQuerySpec(zones=('battlefield',), controller='$actor',
        known_to_actor=True, subtypes_all=(subtype,))
