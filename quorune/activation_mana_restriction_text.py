"""Compile the printed spend restriction of one activated mana ability."""

from __future__ import annotations

import re

from .creature_subtypes import canonical_creature_subtype
from .mana_restrictions import (
    canonical_mana_spend_restriction,
    legacy_mana_spend_restriction_for_tail,
)


def _mana_spend_restriction(effect_text: str) -> str | None:
    lower = " ".join(effect_text.casefold().split())
    marker = "spend this mana only to "
    prohibited_marker = "this mana can't be spent to "
    if prohibited_marker in lower:
        if lower.count(prohibited_marker) != 1 or marker in lower:
            return None
        body = lower.rsplit(prohibited_marker, 1)[1].rstrip(".")
        return legacy_mana_spend_restriction_for_tail(
            prohibited_marker,
            body,
        )
    if marker not in lower or lower.count(marker) != 1:
        return None
    body = lower.rsplit(marker, 1)[1].rstrip(".")
    legacy = legacy_mana_spend_restriction_for_tail(marker, body)
    if legacy is not None:
        return legacy
    if any(
        unsupported in body
        for unsupported in (
            " from ",
            " you don't own",
            " with devoid",
            " with flashback",
            " with mana value",
            "face-down",
            "foretell",
            "kicked",
            "pay a ",
            "pay cumulative",
            "turn ",
            "unlock ",
            "{c}",
            "{x}",
        )
    ):
        return None
    clauses: list[tuple[str, str, tuple[str, ...]]] = []
    paired = re.fullmatch(
        r"cast (?P<spell>.+?) spells? or (?:to )?activate (?P<ability>.+)",
        body,
    )
    reversed_pair = re.fullmatch(
        r"activate (?P<ability>.+?) or cast (?P<spell>.+?) spells?",
        body,
    )
    if paired is not None or reversed_pair is not None:
        match = paired or reversed_pair
        assert match is not None
        spell = _mana_restriction_quality("spell", match.group("spell"))
        ability = _mana_ability_restriction_quality(match.group("ability"))
        if spell is None or ability is None:
            return None
        clauses.extend((spell, ability))
    else:
        spell_only = re.fullmatch(r"cast (?P<spell>.+?) spells?", body)
        ability_only = re.fullmatch(r"activate (?P<ability>.+)", body)
        if spell_only is not None:
            clause = _mana_restriction_quality(
                "spell", spell_only.group("spell")
            )
        elif ability_only is not None:
            clause = _mana_ability_restriction_quality(
                ability_only.group("ability")
            )
        else:
            return None
        if clause is None:
            return None
        clauses.append(clause)
    try:
        return canonical_mana_spend_restriction(tuple(clauses))
    except ValueError:
        return None


_MANA_RESTRICTION_CARD_TYPES = frozenset(
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
_MANA_RESTRICTION_NONCREATURE_SUBTYPES = frozenset(
    {"arcane", "aura", "equipment", "lesson", "omen", "shrine", "vehicle"}
)


def _mana_restriction_terms(value: str) -> tuple[str, ...]:
    normalized = re.sub(r"^(?:a|an)\s+", "", value.strip())
    return tuple(
        part.strip()
        for part in re.split(
            r"\s+and/or\s+|\s+or\s+|,\s*(?:and/or\s+|or\s+)?",
            normalized,
        )
        if part.strip()
    )


def _mana_restriction_quality(
    kind: str,
    value: str,
) -> tuple[str, str, tuple[str, ...]] | None:
    if kind == 'spell' and value.strip() == 'instant and sorcery':
        return 'spell', 'types_any', ('instant', 'sorcery')
    terms = _mana_restriction_terms(value)
    if not terms:
        return None
    singular_types = {
        "artifacts": "artifact",
        "battles": "battle",
        "creatures": "creature",
        "enchantments": "enchantment",
        "instants": "instant",
        "lands": "land",
        "planeswalkers": "planeswalker",
        "sorceries": "sorcery",
    }
    normalized_types = tuple(
        singular_types.get(term, term) for term in terms
    )
    if len(normalized_types) == 1 and normalized_types[0] == "legendary":
        return kind, "supertypes_any", ("legendary",)
    if len(normalized_types) == 1 and normalized_types[0] == "noncreature":
        return kind, "types_none", ("creature",)
    if all(term in _MANA_RESTRICTION_CARD_TYPES for term in normalized_types):
        return kind, "types_any", normalized_types
    subtype_values: list[str] = []
    irregular = {"heroes": "hero"}
    for term in normalized_types:
        candidate = irregular.get(term, term)
        subtype = canonical_creature_subtype(candidate)
        if subtype is None and candidate.endswith("s"):
            subtype = canonical_creature_subtype(candidate[:-1])
        if subtype is None and candidate in _MANA_RESTRICTION_NONCREATURE_SUBTYPES:
            subtype = candidate
        if subtype is None:
            return None
        subtype_values.append(subtype)
    return kind, "subtypes_any", tuple(subtype_values)


def _mana_ability_restriction_quality(
    value: str,
) -> tuple[str, str, tuple[str, ...]] | None:
    normalized = " ".join(value.split())
    if normalized in {"an ability", "abilities"}:
        return "ability", "any", ()
    match = re.fullmatch(
        r"(?:an ability|abilities) of (?:a |an )?(?P<quality>.+?)(?: sources?)?",
        normalized,
    )
    if match is None:
        return None
    return _mana_restriction_quality("ability", match.group("quality"))
