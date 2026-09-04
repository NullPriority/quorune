from __future__ import annotations

import re
from typing import Sequence


_BLOODTHIRST_MECHANIC = "bloodthirst"
_RENOWN_MECHANIC = "renown"
_MODULAR_MECHANIC = "modular"
_FIXED_ENTRY_MECHANICS = ("fading", "graft", "vanishing")

_KEYWORD_WITH_VALUE = re.compile(
    rf"^(?P<name>{re.escape(_BLOODTHIRST_MECHANIC)}|{re.escape(_RENOWN_MECHANIC)}|{re.escape(_MODULAR_MECHANIC)}|{'|'.join(map(re.escape, _FIXED_ENTRY_MECHANICS))}|ward|equip|enchant|bushido|cycling|crew|dredge|kicker|toxic|"
    r"cumulative upkeep|echo|evolve|fabricate|persist|undying|riot|sunburst|unleash|prowess|afterlife|convoke|affinity|morph|megamorph|disguise|bestow|evoke|flashback|unearth|buyback|dash|warp|level up|outlast|reinforce|scavenge)"
    r"(?:\s+(?P<value>.+))?$",
    re.IGNORECASE,
)
_KNOWN_BARE_KEYWORDS = frozenset(
    "deathtouch|defender|double strike|first strike|flash|flying|haste|"
    "flanking|hexproof|indestructible|infect|lifelink|menace|reach|shadow|shroud|"
    "trample|vigilance|wither".split("|")
)
_TYPECYCLING = re.compile(
    r"^(?:basic land|plains|island|swamp|mountain|forest|artifact land|"
    r"wizard|sliver)cycling\s+(?:\{[^{}]+\})+$",
    re.IGNORECASE,
)


def keyword_mechanics(
    text: str,
    card_keywords: Sequence[str],
) -> tuple[str, ...] | None:
    """Recognize a complete Oracle line made only of printed keywords."""

    known = {keyword.casefold() for keyword in card_keywords}
    material = text.rstrip(".").strip()
    if "typecycling" in known and _TYPECYCLING.fullmatch(material):
        return ("cycling",)
    if "enchant" in known and re.fullmatch(
        r"enchant\s+.+",
        material,
        re.IGNORECASE,
    ):
        # Commas inside an Enchant restriction separate target alternatives,
        # not sibling printed keyword abilities. The typed Aura grammar owns
        # the complete restriction after this structural classification.
        return ("enchant",)
    if "flashback" in known and re.match(
        r"flashback(?:\s|[—–])",
        material,
        re.IGNORECASE,
    ):
        # Commas and dashes can be part of a nonmana Flashback cost. Preserve
        # the whole printed ability for the typed Flashback grammar to accept
        # or reject as one source-spanned node.
        return ("flashback",)
    parts = [part.strip() for part in re.split(r"[,;]", material)]
    if not parts:
        return None
    mechanics: list[str] = []
    for part in parts:
        lower = part.casefold()
        if lower == "proliferate":
            # Proliferate is a keyword action whose imperative instruction
            # executes during resolution, not a keyword ability carried by
            # the source. Let the closed resolution grammar own it.
            return None
        if lower.startswith("investigate"):
            # Investigate is a resolution-time keyword action. The fixed
            # token-production grammar owns its complete instruction rather
            # than treating it as a printed keyword ability.
            return None
        if re.fullmatch(r"support\s+.+", lower):
            # Support is a keyword action whose target set depends on whether
            # the instruction's source is a permanent or an instant/sorcery.
            # Let the source-context-aware resolution grammar own it.
            return None
        if re.fullmatch(r"bolster\s+.+", lower):
            # Bolster is a resolution-time keyword action whose eligible
            # creature set depends on current effective toughness.
            return None
        if re.fullmatch(r"amass\s+.+", lower):
            # Amass is a staged resolution-time keyword action. The closed
            # effect grammar owns its subtype and amount.
            return None
        if lower in _KNOWN_BARE_KEYWORDS or lower in known:
            mechanics.append(lower)
            continue
        keyword_part = re.sub(
            r"^(Cumulative upkeep)[—–]\s*",
            r"\1 ",
            part,
            flags=re.IGNORECASE,
        )
        match = _KEYWORD_WITH_VALUE.fullmatch(keyword_part)
        if match and match.group("name").casefold() in known:
            mechanics.append(match.group("name").casefold())
            continue
        if lower.startswith("protection from ") and "protection" in known:
            mechanics.append("protection")
            continue
        return None
    return tuple(mechanics)
