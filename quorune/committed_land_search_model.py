"""Serialized counted land-search shape for committed sacrifices."""

from collections.abc import Mapping
from .replacement.immutable import FrozenMap

COMMITTED_LAND_SEARCH_VERSION = 33
COMMITTED_LAND_SEARCH_CAPABILITY = "library.search.committed_land_sacrifices"
COMMITTED_LAND_SEARCH_FIELD = "_committed_land_sacrifice_search"


def land_sacrifice_search_recipe(*, player="$controller"):
    return {
        "op": "search", "schema_version": COMMITTED_LAND_SEARCH_VERSION, "player": player,
        "zone": "library", "selector": {"types": ["land"]},
        "count": {"minimum": 0, "maximum": None}, "destination": "battlefield",
        "enters_tapped_override": True, "reveal": False, "shuffle_after": True,
    }


def validate_land_sacrifice_search_recipe(effect, *, actor="$controller"):
    if (not isinstance(effect, Mapping) or FrozenMap(effect) != FrozenMap(land_sacrifice_search_recipe(player=actor))
            or type(effect["schema_version"]) is not int or type(effect["count"]["minimum"]) is not int
            or effect["enters_tapped_override"] is not True or effect["reveal"] is not False
            or effect["shuffle_after"] is not True):
        raise ValueError("Committed land sacrifice requires the exact private tapped-land search recipe")
