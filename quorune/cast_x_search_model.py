"""Closed descriptors for a caster's single announced-X creature search."""

from collections.abc import Mapping
from dataclasses import dataclass

from .library_search_model import CAST_X_ZONE_SEARCH_VERSION
from .query_effect_amount_model import CastXAmountSpec
from .replacement.immutable import thaw_value


CAST_X_ZONE_SEARCH_OPERATION = "search_cast_x_library_or_graveyard"
CAST_X_ZONE_SEARCH_MECHANIC = "cast-x-library-graveyard-creature-search"
CAST_X_ZONE_SEARCH_CAPABILITY = "library.search.cast_x_library_graveyard"
CAST_X_ZONE_SEARCH_RULES = ("107.3a", "107.3g", "202.3e", "400.2", "608.2c", "611.2c",
                           "701.23a", "701.23b", "701.23e", "701.23f", "701.24a")
SEARCHED_ZONE_CHOICES = {
    "library": ("library",),
    "graveyard": ("graveyard",),
    "both": ("library", "graveyard"),
}


def cast_x_zone_search_effect(*, maximum=None, player="$controller"):
    return {"op": CAST_X_ZONE_SEARCH_OPERATION, "player": player,
            "search": {"schema_version": 1, "maximum_mana_value": CastXAmountSpec().to_dict() if maximum is None else maximum}}


def cast_x_zone_search_pump(threshold):
    if type(threshold) is not int or threshold < 1:
        raise ValueError("Announced-X team bonus requires a positive threshold")
    return {"op": "pump_controlled_creatures", "amount": CastXAmountSpec().to_dict(),
            "minimum_amount": threshold, "keywords": ["Haste"]}


def decode_cast_x_zone_search(effect, *, actor=None):
    retained = effect.get("search") if isinstance(effect, Mapping) else None
    if not isinstance(retained, Mapping):
        raise ValueError("Announced-X zone search requires its closed descriptor")
    maximum = retained.get("maximum_mana_value")
    if actor is None:
        valid = isinstance(maximum, Mapping) and CastXAmountSpec.from_dict(maximum) == CastXAmountSpec()
    else:
        valid = type(maximum) is int and maximum >= 0
    if (not valid or type(retained.get("schema_version")) is not int
            or thaw_value(effect) != cast_x_zone_search_effect(
                maximum=None if actor is None else maximum, player="$controller" if actor is None else actor)):
        raise ValueError("Announced-X zone search must retain the caster and exact X")
    return maximum


@dataclass(frozen=True, slots=True)
class CastXZoneSearchSelection:
    """The singly counted search after the caster has chosen the zones."""

    zones: tuple[str, ...]
    maximum: int

    def __post_init__(self):
        if self.zones not in SEARCHED_ZONE_CHOICES.values():
            raise ValueError("Announced-X search requires one offered zone selection")
        if type(self.maximum) is not int or self.maximum < 0:
            raise ValueError("Announced-X search requires a nonnegative bound")

    def effect(self):
        return {"op": "search", "schema_version": CAST_X_ZONE_SEARCH_VERSION, "zone": list(self.zones),
                "selector": {"types": ["creature"], "mana_value": {"maximum": self.maximum}},
                "count": {"minimum": 1, "maximum": 1}, "destination": "battlefield",
                "reveal": False, "shuffle_after": "library" in self.zones, "optional": False}

    @classmethod
    def from_effect(cls, effect):
        if not isinstance(effect, Mapping) or not isinstance(effect.get("zone"), (tuple, list)):
            raise ValueError("Announced-X search requires a closed searched-zone descriptor")
        selector = effect.get("selector")
        mana_value = selector.get("mana_value") if isinstance(selector, Mapping) else None
        maximum = mana_value.get("maximum") if isinstance(mana_value, Mapping) else None
        result = cls(tuple(effect["zone"]), maximum)
        fields = thaw_value(effect)
        count = fields.get("count")
        if (fields != result.effect() or type(fields.get("schema_version")) is not int
                or any(type(value) is not int for value in count.values())
                or any(type(fields[key]) is not bool for key in ("reveal", "shuffle_after", "optional"))):
            raise ValueError("Announced-X search selection changed its bound, count or result")
        return result
