from __future__ import annotations

import re
from typing import Any, Mapping

from ..ability_fragments import (
    QueryCharacteristicModifierSpec,
    ability_fragment_to_dict,
)
from ..characteristic_fragments import (
    CharacteristicQuantityScope,
    CharacteristicQuantitySpec,
    PowerToughnessCalculation,
)
from ..keyword_abilities import (
    FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES,
    FIXED_CHARACTERISTIC_KEYWORDS,
)
from ..object_predicate import ObjectQuerySpec
from ..rules.source_references import SourceReferenceSpec
from .creature_subtypes import canonical_creature_subtype


_TRAILING_REMINDER = re.compile(r"\s+\([^()]*\)\.?$")
_COLOR_SYMBOLS = {
    "black": "B",
    "blue": "U",
    "green": "G",
    "red": "R",
    "white": "W",
}
_IRREGULAR_CREATURE_PLURALS = dict(
    value.split(":", 1)
    for value in (
        "aetherborn:aetherborn|allies:ally|dwarves:dwarf|elves:elf|"
        "faeries:faerie|heroes:hero|kithkin:kithkin|merfolk:merfolk|"
        "mice:mouse|myr:myr|phyrexians:phyrexian|treefolk:treefolk|"
        "wolves:wolf"
    ).split("|")
)
_CONDITION_NUMBER_WORDS = {
    "no": 0,
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


def _self_subject_pattern(source_name: str) -> str:
    source = SourceReferenceSpec(source_name).regex_pattern
    return rf"(?:This creature|This permanent|This token|{source})"


def _singular_creature_subtype(plural: str) -> str | None:
    value = plural.casefold()
    if value in _IRREGULAR_CREATURE_PLURALS:
        candidate = _IRREGULAR_CREATURE_PLURALS[value]
        return canonical_creature_subtype(candidate)
    if value.endswith("s") and not value.endswith("ss") and len(value) > 2:
        return canonical_creature_subtype(value[:-1])
    return None


def _fixed_condition_amount(value: str) -> int | None:
    normalized = value.strip().casefold()
    if normalized.isdigit():
        return int(normalized)
    return _CONDITION_NUMBER_WORDS.get(normalized)


_PERMANENT_CARD_TYPES = (
    "artifact",
    "battle",
    "creature",
    "enchantment",
    "land",
    "planeswalker",
)
_LAND_SUBTYPES = frozenset(
    {"plains", "island", "swamp", "mountain", "forest", "gate", "desert"}
)
_NONCREATURE_SUBTYPE_OWNERS = {
    "aura": "enchantment",
    "equipment": "artifact",
    "gate": "land",
    "desert": "land",
}


def _query_quantity_descriptor(
    value: str,
    *,
    zone: str,
) -> tuple[ObjectQuerySpec, bool] | None:
    """Parse one intentionally small public object-description vocabulary."""

    normalized = " ".join(value.strip().casefold().split())
    exclude_source = False
    if normalized.startswith("other "):
        normalized = normalized[len("other ") :]
        exclude_source = True
    elif normalized.startswith("another "):
        normalized = normalized[len("another ") :]
        exclude_source = True
    normalized = re.sub(r"\bcards?$", "", normalized).strip()
    fields: dict[str, Any] = {"zones": (zone,)}
    if normalized in {"", "card"}:
        return (ObjectQuerySpec(**fields), exclude_source)

    singular = {
        "artifacts": "artifact",
        "battles": "battle",
        "creatures": "creature",
        "enchantments": "enchantment",
        "lands": "land",
        "planeswalkers": "planeswalker",
        "permanents": "permanent",
        "tokens": "token",
        "auras": "aura",
        "equipment": "equipment",
        "gates": "gate",
        "deserts": "desert",
    }.get(normalized, normalized)
    words = singular.split()
    if words and words[0] in {"basic", "legendary", "snow"}:
        supertype = words.pop(0)
        fields["supertypes_all"] = (supertype,)
    if words and words[0] in _COLOR_SYMBOLS:
        fields["colors_all"] = (_COLOR_SYMBOLS[words.pop(0)],)
    if words and words[0] in {"token", "nontoken"}:
        fields["token"] = words.pop(0) == "token"
    elif words and words[-1] == "token":
        fields["token"] = True
        words.pop()
    if not words:
        return (ObjectQuerySpec(**fields), exclude_source)

    descriptor = " ".join(words)
    descriptor = {
        "artifacts": "artifact",
        "battles": "battle",
        "creatures": "creature",
        "enchantments": "enchantment",
        "lands": "land",
        "planeswalkers": "planeswalker",
        "permanents": "permanent",
    }.get(descriptor, descriptor)
    if descriptor in _PERMANENT_CARD_TYPES:
        fields["types_all"] = (descriptor,)
    elif descriptor == "permanent":
        fields["types_any"] = _PERMANENT_CARD_TYPES
    elif descriptor in _NONCREATURE_SUBTYPE_OWNERS:
        fields["types_all"] = (_NONCREATURE_SUBTYPE_OWNERS[descriptor],)
        fields["subtypes_all"] = (descriptor,)
    elif descriptor in _LAND_SUBTYPES:
        fields["types_all"] = ("land",)
        fields["subtypes_all"] = (descriptor,)
    elif descriptor == "artifact creature":
        fields["types_all"] = ("artifact", "creature")
    else:
        subtype = canonical_creature_subtype(descriptor)
        if subtype is None:
            subtype = _singular_creature_subtype(descriptor)
        if subtype is None:
            return None
        fields["types_all"] = ("creature",)
        fields["subtypes_all"] = (subtype,)
    return (ObjectQuerySpec(**fields), exclude_source)


def _query_characteristic_quantity(
    value: str,
    *,
    source_name: str,
) -> CharacteristicQuantitySpec | None:
    text = " ".join(value.strip().rstrip(".").split())
    source = SourceReferenceSpec(source_name).regex_pattern
    counter = re.fullmatch(
        rf"(?P<counter>[A-Za-z0-9+/-]+) counters? on "
        rf"(?:it|him|her|this (?:creature|permanent|token)|{source})",
        text,
        re.IGNORECASE,
    )
    if counter is not None:
        try:
            return CharacteristicQuantitySpec(
                scope=CharacteristicQuantityScope.SOURCE_COUNTER,
                counter_name=counter.group("counter"),
            )
        except ValueError:
            return None

    attachment = re.fullmatch(
        rf"(?P<object>Auras?|Equipment) attached to "
        rf"(?:it|him|her|this (?:creature|permanent|token)|{source})",
        text,
        re.IGNORECASE,
    )
    if attachment is not None:
        parsed = _query_quantity_descriptor(
            attachment.group("object"), zone="battlefield"
        )
        if parsed is None:
            return None
        query, _ = parsed
        return CharacteristicQuantitySpec(
            scope=CharacteristicQuantityScope.ATTACHED_TO_SOURCE,
            query=query,
        )

    relations = (
        (
            r"(?P<object>.+?) you control",
            CharacteristicQuantityScope.CONTROLLER_ZONE,
            "battlefield",
        ),
        (
            r"(?P<object>.+?) (?:your opponents control|an opponent controls?)",
            CharacteristicQuantityScope.OPPONENT_ZONES,
            "battlefield",
        ),
        (
            r"(?P<object>.+?) in your opponents' graveyards",
            CharacteristicQuantityScope.OPPONENT_ZONES,
            "graveyard",
        ),
        (
            r"(?P<object>.+?) in an opponent's graveyard",
            CharacteristicQuantityScope.OPPONENT_ZONES,
            "graveyard",
        ),
        (
            r"(?P<object>.+?) in your graveyard",
            CharacteristicQuantityScope.CONTROLLER_ZONE,
            "graveyard",
        ),
        (
            r"(?P<object>cards?) in your hand",
            CharacteristicQuantityScope.CONTROLLER_ZONE,
            "hand",
        ),
        (
            r"(?P<object>.+?) on the battlefield",
            CharacteristicQuantityScope.ALL_ZONES,
            "battlefield",
        ),
    )
    for pattern, scope, zone in relations:
        match = re.fullmatch(pattern, text, re.IGNORECASE)
        if match is None:
            continue
        parsed = _query_quantity_descriptor(match.group("object"), zone=zone)
        if parsed is None:
            return None
        query, exclude_source = parsed
        return CharacteristicQuantitySpec(
            scope=scope,
            query=query,
            exclude_source=exclude_source,
        )
    return None


def _query_characteristic_condition(
    value: str,
    *,
    source_name: str,
) -> tuple[CharacteristicQuantitySpec, int] | None:
    text = " ".join(value.strip().rstrip(".").split())
    threshold = re.fullmatch(
        r"there (?:are|is) (?P<count>one|two|three|four|five|six|seven|"
        r"eight|nine|ten|[1-9]\d*) or more (?P<quantity>.+)",
        text,
        re.IGNORECASE,
    )
    if threshold is not None:
        count = _fixed_condition_amount(threshold.group("count"))
        quantity = _query_characteristic_quantity(
            threshold.group("quantity"), source_name=source_name
        )
        return (quantity, count) if quantity is not None and count else None

    controlled = re.fullmatch(
        r"you control (?P<count>a|an|one|two|three|four|five|six|seven|"
        r"eight|nine|ten|[1-9]\d*)(?: or more)? (?P<object>.+)",
        text,
        re.IGNORECASE,
    )
    if controlled is not None:
        count = _fixed_condition_amount(controlled.group("count"))
        quantity = _query_characteristic_quantity(
            f"{controlled.group('object')} you control",
            source_name=source_name,
        )
        return (quantity, count) if quantity is not None and count else None
    another = re.fullmatch(
        r"you control another (?P<object>.+)", text, re.IGNORECASE
    )
    if another is not None:
        quantity = _query_characteristic_quantity(
            f"other {another.group('object')} you control",
            source_name=source_name,
        )
        return (quantity, 1) if quantity is not None else None

    opponent = re.fullmatch(
        r"an opponent controls (?P<article>a|an|one) (?P<object>.+)",
        text,
        re.IGNORECASE,
    )
    if opponent is not None:
        quantity = _query_characteristic_quantity(
            f"{opponent.group('object')} an opponent control",
            source_name=source_name,
        )
        return (quantity, 1) if quantity is not None else None
    opponent_graveyard = re.fullmatch(
        r"an opponent has (?P<count>one|two|three|four|five|six|seven|"
        r"eight|nine|ten|[1-9]\d*) or more cards in their graveyard",
        text,
        re.IGNORECASE,
    )
    if opponent_graveyard is not None:
        count = _fixed_condition_amount(opponent_graveyard.group("count"))
        quantity = CharacteristicQuantitySpec(
            scope=CharacteristicQuantityScope.OPPONENT_ZONES,
            query=ObjectQuerySpec(zones=("graveyard",)),
        )
        return (quantity, count) if count else None
    return None


def query_self_characteristics_handler(
    oracle_line: str,
    *,
    source_name: str,
) -> tuple[str, Mapping[str, Any], tuple[str, ...]] | None:
    """Compile a closed self modifier over one public typed quantity."""

    text = _TRAILING_REMINDER.sub("", oracle_line.strip()).strip()
    ability_word = re.fullmatch(
        r"[A-Z][A-Za-z0-9' ]{0,80} — (?P<body>.+)", text
    )
    if ability_word is not None:
        text = ability_word.group("body").strip()
    subject = _self_subject_pattern(source_name)
    per_object = re.fullmatch(
        rf"{subject} gets (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) "
        r"for each (?P<quantity>.+?)(?: and has (?P<abilities>.+))?\.?$",
        text,
        re.IGNORECASE,
    )
    if per_object is not None:
        quantity = _query_characteristic_quantity(
            per_object.group("quantity"), source_name=source_name
        )
        # Keyword grants combined with a per-object modifier are grammatically
        # fixed, not multiplied. They remain residual until represented as two
        # independently applicable effects.
        if quantity is None or per_object.group("abilities") is not None:
            return None
        fragment = QueryCharacteristicModifierSpec(
            quantity=quantity,
            calculation=PowerToughnessCalculation.PER_MATCHING_OBJECT,
            power=int(per_object.group("power")),
            toughness=int(per_object.group("toughness")),
        )
        abilities: tuple[str, ...] = ()
    else:
        threshold = re.fullmatch(
            rf"{subject} gets (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+)"
            r"(?: and has (?P<abilities>.+?))? as long as (?P<condition>.+?)\.?$",
            text,
            re.IGNORECASE,
        )
        if threshold is None:
            return None
        parsed = _query_characteristic_condition(
            threshold.group("condition"), source_name=source_name
        )
        if parsed is None:
            return None
        quantity, minimum = parsed
        raw_abilities = threshold.group("abilities")
        abilities = ()
        if raw_abilities:
            normalized = re.sub(
                r",?\s+and\s+", ",", raw_abilities, flags=re.IGNORECASE
            )
            abilities = tuple(
                value.strip().title()
                for value in normalized.split(",")
                if value.strip()
            )
            if (
                not abilities
                or len(set(abilities)) != len(abilities)
                or any(
                    value not in FIXED_CHARACTERISTIC_KEYWORDS
                    for value in abilities
                )
            ):
                return None
        fragment = QueryCharacteristicModifierSpec(
            quantity=quantity,
            calculation=PowerToughnessCalculation.FIXED_IF_THRESHOLD,
            power=int(threshold.group("power")),
            toughness=int(threshold.group("toughness")),
            minimum_count=minimum,
            add_abilities=abilities,
        )
    capabilities = {
        "continuous.characteristics.query_count_modifier",
        *(
            capability
            for ability in abilities
            for capability in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[
                ability
            ]
        ),
    }
    return (
        "continuous-self-query-characteristics-v1",
        {
            "handler_id": "ability.static.query-characteristic-modifier.v1",
            "schema_version": 1,
            "event": "continuous",
            "fragment": ability_fragment_to_dict(fragment),
        },
        tuple(sorted(capabilities)),
    )


__all__ = ["query_self_characteristics_handler"]
