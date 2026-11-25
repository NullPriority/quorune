from __future__ import annotations

"""Closed values for one resolution-created indefinite keyword grant."""


class ZoneObjectKeywordGrantError(ValueError):
    """A zone-object keyword descriptor is outside the represented family."""


# This is the exact reviewed indefinite-grant family, not the broader keyword-
# counter vocabulary. Each addition requires its own executable keyword
# capability and interaction evidence before compiler or runtime acceptance.
ZONE_OBJECT_KEYWORDS = frozenset(
    {
        "first strike",
        "flying",
        "haste",
        "trample",
        "vigilance",
    }
)


def controlled_creature_indestructible_effect():
    return {"op": "grant_zone_object_keyword", "schema_version": 2,
            "scope": "controlled_creature_set", "keyword": "indestructible"}


def validate_controlled_creature_indestructible(effect):
    if dict(effect) != controlled_creature_indestructible_effect() or type(effect["schema_version"]) is not int:
        raise ZoneObjectKeywordGrantError("Indefinite Indestructible requires the exact controlled creature set")


def zone_object_intent_keyword(object_ref, keyword, *, controlled_creature_set):
    if type(controlled_creature_set) is not bool:
        raise ZoneObjectKeywordGrantError("Zone-object keyword intent scope must be a boolean")
    if controlled_creature_set:
        if object_ref is not None or keyword != "indestructible":
            raise ZoneObjectKeywordGrantError("Controlled creature Indestructible has no chosen object")
        return keyword
    if type(object_ref) is not str or not object_ref:
        raise ZoneObjectKeywordGrantError("A zone-object keyword intent requires its object")
    return normalized_zone_object_keyword(keyword)


def normalized_zone_object_keyword(value: object) -> str:
    """Return one closed executable keyword or fail before mutation."""

    if type(value) is not str:
        raise ZoneObjectKeywordGrantError(
            "Zone-object keyword grants require one string keyword"
        )
    keyword = " ".join(value.casefold().split())
    if keyword not in ZONE_OBJECT_KEYWORDS:
        raise ZoneObjectKeywordGrantError(
            "Zone-object keyword grant is outside the represented vocabulary"
        )
    return keyword


__all__ = [
    "ZONE_OBJECT_KEYWORDS",
    "ZoneObjectKeywordGrantError",
    "normalized_zone_object_keyword",
]
