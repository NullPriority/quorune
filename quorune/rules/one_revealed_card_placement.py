"""Closed results of revealing one controller library card."""

from dataclasses import dataclass
from typing import Any, Mapping


REVEAL_ONE_TOP_OPERATION = "reveal_controller_library_top_for_placement"
PLACE_REVEALED_TOP_OPERATION = "place_revealed_controller_library_top"
ENTER_REVEALED_TOP_OPERATION = "put_revealed_library_top_onto_battlefield"
REVEALED_LAND_OR_HAND_CAPABILITY = "library.top.revealed_land_or_hand"
REVEALED_LAND_OR_HAND_VERSION = 2
REVEAL_ONE_TOP_CAPABILITIES = (
    "library.select.fixed_controller",
    "library.select.battlefield_controller",
    "effect.choice.optional_fixed",
)
_SUPPORTED = {
    ("creature", False, "optional_bottom"),
    ("creature", False, "graveyard"),
    ("permanent", True, "optional_bottom"),
    ("land", False, "hand"),
}
_PERMANENT_TYPES = frozenset({"artifact", "battle", "creature", "enchantment", "land", "planeswalker"})


@dataclass(frozen=True, slots=True)
class OneRevealedCardPlacement:
    required_type: str
    entry_optional: bool
    fallback: str

    def __post_init__(self) -> None:
        if type(self.entry_optional) is not bool or (self.required_type, self.entry_optional, self.fallback) not in _SUPPORTED:
            raise ValueError("A revealed top-card result needs its exact type test and destinations")

    def effect(self) -> dict[str, Any]:
        return {
            "op": REVEAL_ONE_TOP_OPERATION, "schema_version": self.schema_version, "player": "$controller",
            "required_type": self.required_type, "entry_optional": self.entry_optional, "fallback": self.fallback,
        }

    @property
    def schema_version(self) -> int:
        return REVEALED_LAND_OR_HAND_VERSION if self.fallback == "hand" else 1

    @property
    def capabilities(self) -> tuple[str, ...]:
        if self.fallback == "hand":
            return (*REVEAL_ONE_TOP_CAPABILITIES, REVEALED_LAND_OR_HAND_CAPABILITY)
        return REVEAL_ONE_TOP_CAPABILITIES

    @classmethod
    def from_effect(cls, effect: Mapping[str, Any], *, player: str = "$controller"):
        if (not isinstance(effect, Mapping)
                or set(effect) != {"op", "schema_version", "player", "required_type", "entry_optional", "fallback"}
                or effect["op"] != REVEAL_ONE_TOP_OPERATION
                or type(effect["schema_version"]) is not int
                or effect["player"] != player
                or type(effect["required_type"]) is not str or type(effect["fallback"]) is not str):
            raise ValueError("The revealed top-card result has a closed descriptor")
        spec = cls(effect["required_type"], effect["entry_optional"], effect["fallback"])
        if effect["schema_version"] != spec.schema_version:
            raise ValueError("The revealed top-card result changed its descriptor version")
        return spec

    def matches(self, types) -> bool:
        return (bool(_PERMANENT_TYPES.intersection(types)) if self.required_type == "permanent"
                else self.required_type in types)
