from __future__ import annotations

from dataclasses import dataclass

from . import aerial_blocking
from .landwalk import (
    LANDWALK_KEYWORDS, LandwalkLandCharacteristics,
    basic_landwalk_block_verdict, landwalk_block_verdict,
)


FEAR_KEYWORD = "fear"
HORSEMANSHIP_KEYWORD = "horsemanship"
INTIMIDATE_KEYWORD = "intimidate"
SHADOW_KEYWORD = "shadow"
SKULK_KEYWORD = "skulk"
_CARD_TYPES = frozenset(
    {
        "artifact",
        "battle",
        "creature",
        "enchantment",
        "instant",
        "kindred",
        "land",
        "planeswalker",
        "sorcery",
    }
)
_COLORS = frozenset({"B", "G", "R", "U", "W"})
_REJECTION_REASONS = frozenset(
    {
        "attacker_has_fear",
        "attacker_has_shadow",
        "attacker_has_horsemanship",
        "attacker_has_intimidate",
        "attacker_has_skulk",
        "external_greater_power_restriction",
        "blocker_has_shadow",
        "attacker_has_flying",
        "blocker_has_self_counter_prohibition",
        "attacker_has_plainswalk",
        "attacker_has_islandwalk",
        "attacker_has_swampwalk",
        "attacker_has_mountainwalk",
        "attacker_has_forestwalk",
    } | {"attacker_has_" + keyword.replace(" ", "_") for keyword in LANDWALK_KEYWORDS}
)


class CombatEvasionRuleError(ValueError):
    """A current combatant snapshot or represented restriction is invalid."""


def _canonical_terms(
    value: object,
    *,
    label: str,
    vocabulary: frozenset[str] | None = None,
) -> frozenset[str]:
    if type(value) is not frozenset or any(
        not isinstance(term, str)
        or not term
        or term != term.strip()
        for term in value
    ):
        raise CombatEvasionRuleError(f"Canonical {label} snapshot is malformed")
    if vocabulary is not None and not value.issubset(vocabulary):
        raise CombatEvasionRuleError(
            f"Canonical {label} snapshot exceeds the closed vocabulary"
        )
    return value


@dataclass(frozen=True, slots=True)
class CombatantEvasionCharacteristics:
    """Current public characteristics needed by ordinary evasion keywords."""

    keywords: frozenset[str]
    colors: frozenset[str]
    card_types: frozenset[str]
    power: int | None

    def __post_init__(self) -> None:
        keywords = _canonical_terms(self.keywords, label="keyword")
        if any(keyword != keyword.casefold() for keyword in keywords):
            raise CombatEvasionRuleError(
                "Canonical keyword snapshot must be case-folded"
            )
        _canonical_terms(
            self.colors,
            label="color",
            vocabulary=_COLORS,
        )
        card_types = _canonical_terms(
            self.card_types,
            label="card type",
            vocabulary=_CARD_TYPES,
        )
        if any(card_type != card_type.casefold() for card_type in card_types):
            raise CombatEvasionRuleError(
                "Canonical card-type snapshot must be case-folded"
            )
        if "creature" not in card_types:
            raise CombatEvasionRuleError(
                "Combat evasion participants must be current creatures"
            )
        if self.power is not None and (
            isinstance(self.power, bool) or not isinstance(self.power, int)
        ):
            raise CombatEvasionRuleError(
                "Current combatant power must be an exact integer or unresolved"
            )


@dataclass(frozen=True, slots=True)
class CombatEvasionVerdict:
    """Cumulative verdict for represented keyword block restrictions."""

    allowed: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool:
            raise ValueError("Combat evasion verdict allowed must be boolean")
        if self.allowed != (self.reason is None):
            raise ValueError("An allowed combat evasion verdict has no reason")
        if self.reason is not None and self.reason not in _REJECTION_REASONS:
            raise ValueError("Unknown combat evasion rejection reason")


def combat_evasion_verdict(
    attacker: CombatantEvasionCharacteristics,
    blocker: CombatantEvasionCharacteristics,
    defending_land_types: frozenset[str] | tuple[LandwalkLandCharacteristics, ...],
    *, ignored_landwalk_keywords: frozenset[str] = frozenset(),
) -> CombatEvasionVerdict:
    """Compose represented ordinary evasion restrictions fail-closed."""

    if not isinstance(attacker, CombatantEvasionCharacteristics) or not isinstance(
        blocker, CombatantEvasionCharacteristics
    ):
        raise CombatEvasionRuleError(
            "Combat evasion requires typed current combatant snapshots"
        )

    # Validate every represented family before returning a restriction from
    # another family, so an unsupported landwalk variant cannot be masked.
    landwalk = (basic_landwalk_block_verdict(attacker.keywords, defending_land_types,
            ignored_keywords=ignored_landwalk_keywords)
        if isinstance(defending_land_types, frozenset)
        else landwalk_block_verdict(attacker.keywords, defending_land_types,
            ignored_keywords=ignored_landwalk_keywords))
    aerial = aerial_blocking.aerial_block_verdict(
        attacker.keywords,
        blocker.keywords,
    )
    if SKULK_KEYWORD in attacker.keywords and (
        attacker.power is None or blocker.power is None
    ):
        raise CombatEvasionRuleError(
            "Skulk requires resolved current attacker and blocker power"
        )
    if SHADOW_KEYWORD in attacker.keywords and SHADOW_KEYWORD not in blocker.keywords:
        return CombatEvasionVerdict(False, "attacker_has_shadow")
    if SHADOW_KEYWORD in blocker.keywords and SHADOW_KEYWORD not in attacker.keywords:
        return CombatEvasionVerdict(False, "blocker_has_shadow")
    if (
        HORSEMANSHIP_KEYWORD in attacker.keywords
        and HORSEMANSHIP_KEYWORD not in blocker.keywords
    ):
        return CombatEvasionVerdict(False, "attacker_has_horsemanship")
    if (
        FEAR_KEYWORD in attacker.keywords
        and "artifact" not in blocker.card_types
        and "B" not in blocker.colors
    ):
        return CombatEvasionVerdict(False, "attacker_has_fear")
    if (
        INTIMIDATE_KEYWORD in attacker.keywords
        and "artifact" not in blocker.card_types
        and not attacker.colors.intersection(blocker.colors)
    ):
        return CombatEvasionVerdict(False, "attacker_has_intimidate")
    if (
        SKULK_KEYWORD in attacker.keywords
        and blocker.power is not None
        and attacker.power is not None
        and blocker.power > attacker.power
    ):
        return CombatEvasionVerdict(False, "attacker_has_skulk")
    if not aerial.allowed:
        return CombatEvasionVerdict(False, aerial.reason)
    if not landwalk.allowed:
        return CombatEvasionVerdict(False, landwalk.reason)
    return CombatEvasionVerdict(True)


__all__ = [
    "CombatantEvasionCharacteristics",
    "CombatEvasionVerdict",
    "CombatEvasionRuleError",
    "FEAR_KEYWORD",
    "HORSEMANSHIP_KEYWORD",
    "INTIMIDATE_KEYWORD",
    "SHADOW_KEYWORD",
    "SKULK_KEYWORD",
    "combat_evasion_verdict",
]
