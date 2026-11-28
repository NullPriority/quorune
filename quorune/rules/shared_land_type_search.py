"""Offer and validate the same private two-basic-land subtype relation."""

from ..basic_land_types import LAND_SUBTYPES
from ..characteristic_evaluation import type_parts
from ..errors import GameRuleError
from ..library_search_model import SHARED_LAND_TYPE_SEARCH_FIELD


def _basic_land_subtypes(host, card):
    data = host._effective_card_data(card)
    types, subtypes, supertypes = type_parts(str(data.get("type_line") or ""))
    if card.zone != "library" or "land" not in types or "basic" not in supertypes:
        raise GameRuleError("The shared land type search requires current basic lands in the library")
    return LAND_SUBTYPES.intersection(subtypes)


def shared_land_type_search_choice(host, effect, seat, options):
    if effect.get(SHARED_LAND_TYPE_SEARCH_FIELD) is not True:
        return {}
    memberships = {
        option["id"]: sorted(_basic_land_subtypes(host, host._resolve_object(
            seat, option["id"], zones={"library"}, owned_only=True,
        )))
        for option in options
    }
    return {"label": "Search basic lands (two must share a land type)",
            "pair_constraint": {"kind": SHARED_LAND_TYPE_SEARCH_FIELD, "land_types": memberships}}


def validate_shared_land_type_search(host, effect, selected):
    if effect.get(SHARED_LAND_TYPE_SEARCH_FIELD) is not True or len(selected) < 2:
        return
    first, second = selected
    if _basic_land_subtypes(host, first).isdisjoint(_basic_land_subtypes(host, second)):
        raise GameRuleError("Choose two basic lands that share a land type")
