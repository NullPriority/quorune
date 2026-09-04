from __future__ import annotations

"""Typed production and spending contexts for restricted mana units."""

from .characteristic_evaluation import type_parts


def mana_restriction_allows(
    restriction: str,
    spend_context: str | None,
) -> bool:
    """Return whether one typed restricted unit may pay in this context."""

    is_spell = bool(spend_context and "spell" in spend_context)
    is_artifact = bool(
        spend_context and spend_context.startswith("artifact")
    )
    is_creature = bool(spend_context and "creature" in spend_context)
    is_legendary = bool(spend_context and "legendary" in spend_context)
    if restriction == "artifact_spell_or_ability":
        return (
            (is_spell and is_artifact)
            or spend_context == "artifact_ability"
        )
    if restriction == "artifact_spell_only":
        return is_spell and is_artifact
    if restriction == "creature_spell_only":
        return is_spell and is_creature
    if restriction == "nonartifact_spell_prohibited":
        return not (is_spell and not is_artifact)
    if restriction == "legendary_spell_uncounterable":
        return is_spell and is_legendary
    return False


def spell_mana_spend_context(type_line: str) -> str:
    """Encode the public spell types consumed by restriction predicates."""

    types, _, supertypes = type_parts(type_line)
    artifact = "artifact" in types
    creature = "creature" in types
    legendary = "legendary" in supertypes
    values = [
        *(("artifact",) if artifact else ()),
        *(("creature",) if creature else ()),
        *(("legendary",) if legendary else ()),
        "spell",
    ]
    return (
        "_".join(values)
        if artifact or creature or legendary
        else "nonartifact_spell"
    )


__all__ = ["mana_restriction_allows", "spell_mana_spend_context"]
