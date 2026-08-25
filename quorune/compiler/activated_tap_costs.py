from __future__ import annotations

"""Closed fixed selected-permanent tap costs for activated abilities."""

from dataclasses import replace
import re

from ..abilities import ActivatedAbility, CostChoice
from ..creature_subtypes import canonical_creature_subtype
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from ..rules.activation_costs import FIXED_TAP_ACTIVATION_COST_KIND


_FIXED_NUMBERS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_PERMANENT_CARD_TYPES = frozenset(
    {"artifact", "battle", "creature", "enchantment", "land", "planeswalker"}
)
_NONCREATURE_SUBTYPE_SURFACES = {
    "food": "food",
    "foods": "food",
    "gate": "gate",
    "gates": "gate",
    "forest": "forest",
    "forests": "forest",
    "island": "island",
    "islands": "island",
    "mountain": "mountain",
    "mountains": "mountain",
    "plains": "plains",
    "swamp": "swamp",
    "swamps": "swamp",
}
_IRREGULAR_CREATURE_PLURALS = {
    "allies": "ally",
    "dwarves": "dwarf",
    "elves": "elf",
    "werewolves": "werewolf",
}
_FIXED_TAP_COST = re.compile(
    r"^tap\s+(?:(?P<another>another)|"
    r"(?P<count>a|an|one|two|three|four|five|six|seven|eight|nine|ten|[1-9][0-9]*)"
    r"(?:\s+(?P<other>other))?)\s+untapped\s+"
    r"(?P<quality>[A-Za-z][A-Za-z'’ -]*)\s+you\s+control$",
    re.IGNORECASE,
)


def _fixed_count(value: str) -> int:
    normalized = value.casefold()
    if normalized in _FIXED_NUMBERS:
        return _FIXED_NUMBERS[normalized]
    return int(normalized) if normalized.isdigit() else 0


def _canonical_creature_surface(value: str, *, count: int) -> str | None:
    normalized = " ".join(value.casefold().replace("’", "'").split())
    direct = canonical_creature_subtype(normalized)
    if direct is not None:
        return direct
    if count == 1:
        return None
    candidates = []
    irregular = _IRREGULAR_CREATURE_PLURALS.get(normalized)
    if irregular is not None:
        candidates.append(irregular)
    if normalized.endswith("ies"):
        candidates.append(f"{normalized[:-3]}y")
    if normalized.endswith("ves"):
        candidates.extend((f"{normalized[:-3]}f", f"{normalized[:-3]}fe"))
    if normalized.endswith("s") and not normalized.endswith("ss"):
        candidates.append(normalized[:-1])
    matches = {
        subtype
        for candidate in candidates
        if (subtype := canonical_creature_subtype(candidate)) is not None
    }
    return next(iter(matches)) if len(matches) == 1 else None


def fixed_tap_activation_cost_choice(cost_text: str) -> CostChoice | None:
    """Compile one fixed tap selection over one card type or pinned subtype."""

    match = _FIXED_TAP_COST.fullmatch(" ".join(cost_text.strip(" .").split()))
    if match is None:
        return None
    another = match.group("another") is not None or match.group("other") is not None
    count = 1 if match.group("another") is not None else _fixed_count(match.group("count"))
    quality = " ".join(match.group("quality").casefold().split())
    fields: dict[str, tuple[str, ...]] = {}
    singular_type = quality.removesuffix("s")
    if singular_type in _PERMANENT_CARD_TYPES and (
        (count == 1 and quality == singular_type)
        or (count > 1 and quality == f"{singular_type}s")
    ):
        fields["types_all"] = (singular_type,)
    else:
        subtype = _NONCREATURE_SUBTYPE_SURFACES.get(quality)
        if subtype is not None:
            if count == 1 and quality.endswith("s") and quality != "plains":
                return None
            if count > 1 and quality in {"food", "gate", "forest", "island", "mountain", "swamp"}:
                return None
        else:
            subtype = _canonical_creature_surface(quality, count=count)
        if subtype is None:
            return None
        fields["subtypes_all"] = (subtype,)
    predicate = ObjectQuerySpec(
        zones=("battlefield",),
        controller="$actor",
        tapped=False,
        known_to_actor=True,
        exclude_ref="$source" if another else None,
        **fields,
    )
    return CostChoice(
        kind=FIXED_TAP_ACTIVATION_COST_KIND,
        count=count,
        zone="battlefield",
        another=another,
        predicate=FrozenMap(predicate.to_dict()),
    )


def fixed_activated_tap_cost(ability: ActivatedAbility) -> ActivatedAbility:
    """Lower one independently closed fixed tap cost on a stack activation."""

    if (
        ability.mana_ability
        or ability.zones != ("battlefield",)
        or ability.complex_symbols
        or ability.tap_source
        or ability.untap_source
        or ability.discard_source
        or ability.sacrifice_source
        or ability.exile_source
        or ability.life_payment
        or ability.energy_payment
        or ability.loyalty_delta is not None
        or ability.choices
        or len(ability.uncompiled_costs) != 1
    ):
        return ability
    choice = fixed_tap_activation_cost_choice(ability.uncompiled_costs[0])
    if choice is None:
        return ability
    return replace(ability, choices=(choice,), uncompiled_costs=())


__all__ = [
    "fixed_activated_tap_cost",
    "fixed_tap_activation_cost_choice",
]
