from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from ..characteristic_fragments import (
    CharacteristicQuantityScope,
    CharacteristicQuantitySpec,
)
from ..continuous_conditions import (
    FixedPublicStateConditionKind,
    FixedPublicStateConditionSpec,
)
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from ..rules.source_references import SourceReferenceSpec
from .creature_subtypes import canonical_creature_subtype


_CONTROLLED_CREATURE_MODIFIER = re.compile(
    r"^(?P<other>Other )?"
    r"(?:(?P<qualifier>[Aa]rtifact|[Ww]hite|[Bb]lue|[Bb]lack|"
    r"[Rr]ed|[Gg]reen|[Ll]egendary|"
    r"[A-Z][A-Za-z'-]*(?: [A-Z][A-Za-z'-]*)?) )?"
    r"[Cc]reatures you control get (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+)"
    r"(?P<until> until end of turn)?\.?$"
)
_CONTROLLED_SUBTYPE_PLURAL_MODIFIER = re.compile(
    r"^(?P<other>Other )?(?P<plural>[A-Z][A-Za-z'-]*) you control get "
    r"(?P<power>[+-]\d+)/(?P<toughness>[+-]\d+)"
    r"(?P<until> until end of turn)?\.?$"
)
_IRREGULAR_CREATURE_PLURALS = dict(
    value.split(":", 1)
    for value in (
        "aetherborn:aetherborn|allies:ally|dwarves:dwarf|elves:elf|"
        "faeries:faerie|heroes:hero|kithkin:kithkin|merfolk:merfolk|"
        "mice:mouse|myr:myr|phyrexians:phyrexian|treefolk:treefolk|"
        "wolves:wolf"
    ).split("|")
)
_STATEFUL_CREATURE_QUALIFIER = re.compile(
    r"^(?:attacking|blocking|enchanted|equipped|modified|tapped|untapped)$"
)
_STATE_QUALIFIED_BATTLEFIELD_SUBJECT = re.compile(
    r"^(?P<other>Other )?"
    r"(?P<state>Attacking|Blocking|Enchanted|Equipped|Modified|Tapped|Untapped) "
    r"(?P<subject>.+)$",
    re.IGNORECASE,
)
_CONTROLLED_CREATURE_TOKENS = re.compile(
    r"^(?P<other>Other )?Creature tokens you control$",
    re.IGNORECASE,
)
_COUNTER_QUALIFIED_CONTROLLED_CREATURES = re.compile(
    r"^Each (?P<other>other )?creature you control with a \+1/\+1 "
    r"counter on it$",
    re.IGNORECASE,
)
_CONTROLLED_MULTICOLORED_CREATURES = re.compile(
    r"^(?P<other>Other )?Multicolored creatures you control$",
    re.IGNORECASE,
)
_OPPONENT_CONTROLLED_CREATURES = re.compile(
    r"^Creatures your opponents control$",
    re.IGNORECASE,
)
_CONTROLLED_SUBTYPE_TOKENS = re.compile(
    r"^(?P<other>Other )?(?P<subtype>[A-Z][A-Za-z'-]*(?: [A-Z][A-Za-z'-]*)?) "
    r"tokens you control$"
)
_CONTROLLED_SUBJECT = re.compile(
    r"^(?P<other>Other )?"
    r"(?:(?P<quality>Artifact|Black|Blue|Colorless|Green|Land|Legendary|"
    r"Nontoken|Red|Token|White) )?"
    r"(?P<subject>creatures|permanents|artifacts|lands) you control$",
    re.IGNORECASE,
)
_CONTROLLED_SUBTYPE_CREATURES = re.compile(
    r"^(?P<other>Other )?(?P<subtype>[A-Z][A-Za-z'-]*(?: [A-Z][A-Za-z'-]*)?) "
    r"creatures you control$"
)
_CONTROLLED_PLURAL_SUBJECT = re.compile(
    r"^(?P<other>Other )?(?P<plural>[A-Z][A-Za-z'-]*) you control$"
)
_GLOBAL_SUBJECT = re.compile(
    r"^(?P<other>Other )?(?:All )?"
    r"(?:(?P<quality>Black|Blue|Green|Red|White) )?"
    r"(?P<subject>creatures|artifacts|lands)$",
    re.IGNORECASE,
)
_GLOBAL_SUBTYPE_CREATURES = re.compile(
    r"^(?P<other>Other )?(?:All )?"
    r"(?P<subtype>[A-Z][A-Za-z'-]*(?: [A-Z][A-Za-z'-]*)?) creatures$"
)
_GLOBAL_PLURAL_SUBJECT = re.compile(
    r"^(?P<other>Other )?All (?P<plural>[A-Z][A-Za-z'-]*)$"
)
_TRAILING_REMINDER = re.compile(r"\s+\([^()]*\)\.?$")
_COLOR_SYMBOLS = {
    "black": "B",
    "blue": "U",
    "green": "G",
    "red": "R",
    "white": "W",
}
_CARD_TYPE_WORDS = frozenset(
    {
        "artifact",
        "battle",
        "creature",
        "enchantment",
        "land",
        "planeswalker",
    }
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


def _singular_creature_subtype(plural: str) -> str | None:
    value = plural.casefold()
    if value in _IRREGULAR_CREATURE_PLURALS:
        return canonical_creature_subtype(_IRREGULAR_CREATURE_PLURALS[value])
    if value.endswith("s") and not value.endswith("ss") and len(value) > 2:
        return canonical_creature_subtype(value[:-1])
    return None


def _state_qualified_battlefield_query(
    text: str,
) -> tuple[str, ObjectQuerySpec, bool] | None:
    match = _STATE_QUALIFIED_BATTLEFIELD_SUBJECT.fullmatch(text)
    if match is None:
        return None
    nested_text = (
        ("Other " if match.group("other") else "") + match.group("subject")
    )
    if nested_text.casefold() in {
        "tokens you control",
        "other tokens you control",
    }:
        nested = (
            "source_controller",
            ObjectQuerySpec(zones=("battlefield",), token=True),
            bool(match.group("other")),
        )
    else:
        nested = fixed_battlefield_query_subject(nested_text)
    if nested is None:
        return None
    relation, predicate, exclude_source = nested
    if predicate.state_predicate is not None:
        # The bounded grammar owns one state qualifier. Combining state or
        # counter predicates requires an explicit conjunction schema.
        return None
    state = match.group("state").casefold()
    state_fields = (
        {"tapped": state == "tapped"}
        if state in {"tapped", "untapped"}
        else {state: True}
    )
    return (
        relation,
        replace(
            predicate,
            state_predicate=PermanentStatePredicateSpec(**state_fields),
        ),
        exclude_source,
    )


def _controlled_battlefield_query(
    text: str,
) -> tuple[str, ObjectQuerySpec, bool] | None:
    fields: dict[str, Any] = {"zones": ("battlefield",)}
    exclude_source = False
    if (match := _COUNTER_QUALIFIED_CONTROLLED_CREATURES.fullmatch(text)):
        exclude_source = bool(match.group("other"))
        fields.update(
            types_all=("creature",),
            state_predicate=PermanentStatePredicateSpec(
                counter_name="+1/+1",
                minimum_counter_count=1,
            ),
        )
    elif (match := _CONTROLLED_MULTICOLORED_CREATURES.fullmatch(text)):
        exclude_source = bool(match.group("other"))
        fields.update(types_all=("creature",), minimum_color_count=2)
    elif (match := _CONTROLLED_CREATURE_TOKENS.fullmatch(text)):
        exclude_source = bool(match.group("other"))
        fields.update(types_all=("creature",), token=True)
    elif (match := _CONTROLLED_SUBTYPE_TOKENS.fullmatch(text)):
        subtype = canonical_creature_subtype(match.group("subtype"))
        if subtype is None:
            return None
        exclude_source = bool(match.group("other"))
        fields.update(
            types_all=("creature",),
            subtypes_all=(subtype,),
            token=True,
        )
    elif (match := _CONTROLLED_SUBJECT.fullmatch(text)):
        exclude_source = bool(match.group("other"))
        subject_kind = match.group("subject").casefold()
        quality = (match.group("quality") or "").casefold()
        if quality and subject_kind != "creatures":
            return None
        if subject_kind != "permanents":
            fields["types_all"] = (subject_kind.removesuffix("s"),)
        if quality in {"artifact", "land"}:
            fields["types_all"] = (quality, "creature")
        elif quality in _COLOR_SYMBOLS:
            fields.update(
                types_all=("creature",),
                colors_all=(_COLOR_SYMBOLS[quality],),
            )
        elif quality == "colorless":
            fields.update(types_all=("creature",), colorless=True)
        elif quality == "legendary":
            fields.update(
                types_all=("creature",),
                supertypes_all=("legendary",),
            )
        elif quality in {"nontoken", "token"}:
            fields.update(
                types_all=("creature",),
                token=quality == "token",
            )
    else:
        return _controlled_subtype_battlefield_query(text)
    return "source_controller", ObjectQuerySpec(**fields), exclude_source


def _controlled_subtype_battlefield_query(
    text: str,
) -> tuple[str, ObjectQuerySpec, bool] | None:
    subtype: str | None
    if (match := _CONTROLLED_SUBTYPE_CREATURES.fullmatch(text)):
        subtype = canonical_creature_subtype(match.group("subtype"))
        fields: dict[str, Any] = {
            "zones": ("battlefield",),
            "types_all": ("creature",),
        }
    elif (match := _CONTROLLED_PLURAL_SUBJECT.fullmatch(text)):
        plural = match.group("plural")
        if plural.casefold() == "vehicles":
            subtype = "vehicle"
            fields = {
                "zones": ("battlefield",),
                "types_all": ("artifact",),
            }
        else:
            subtype = _singular_creature_subtype(plural)
            fields = {
                "zones": ("battlefield",),
                "types_all": ("creature",),
            }
    else:
        return None
    if subtype is None:
        return None
    fields["subtypes_all"] = (subtype,)
    return (
        "source_controller",
        ObjectQuerySpec(**fields),
        bool(match.group("other")),
    )


def _global_battlefield_query(
    text: str,
) -> tuple[str, ObjectQuerySpec, bool] | None:
    fields: dict[str, Any] = {"zones": ("battlefield",)}
    if _OPPONENT_CONTROLLED_CREATURES.fullmatch(text):
        return (
            "source_opponents",
            ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
            False,
        )
    if (match := _GLOBAL_SUBJECT.fullmatch(text)):
        subject_kind = match.group("subject").casefold()
        quality = (match.group("quality") or "").casefold()
        if quality and subject_kind != "creatures":
            return None
        fields["types_all"] = (
            ("creature",)
            if subject_kind == "creatures"
            else (subject_kind.removesuffix("s"),)
        )
        if quality:
            fields["colors_all"] = (_COLOR_SYMBOLS[quality],)
    elif (match := _GLOBAL_SUBTYPE_CREATURES.fullmatch(text)):
        subtype = canonical_creature_subtype(match.group("subtype"))
        if subtype is None:
            return None
        fields.update(types_all=("creature",), subtypes_all=(subtype,))
    elif (match := _GLOBAL_PLURAL_SUBJECT.fullmatch(text)):
        subtype = _singular_creature_subtype(match.group("plural"))
        if subtype is None:
            return None
        fields.update(types_all=("creature",), subtypes_all=(subtype,))
    else:
        return None
    return "any", ObjectQuerySpec(**fields), bool(match.group("other"))


def fixed_battlefield_query_subject(
    subject: str,
) -> tuple[str, ObjectQuerySpec, bool] | None:
    """Parse one fixed public battlefield set shared by layers 6 and 7c."""

    text = subject.strip()
    if _STATE_QUALIFIED_BATTLEFIELD_SUBJECT.fullmatch(text):
        return _state_qualified_battlefield_query(text)
    controlled = _controlled_battlefield_query(text)
    if controlled is not None:
        return controlled
    return _global_battlefield_query(text)


def controlled_creature_fixed_modifier(
    oracle_line: str,
    *,
    until_end_of_turn: bool,
) -> tuple[ObjectQuerySpec, int, int, bool] | None:
    """Parse one closed fixed modifier over controlled creatures."""

    match = _CONTROLLED_CREATURE_MODIFIER.fullmatch(oracle_line.strip())
    subtype_plural = False
    if match is None:
        match = _CONTROLLED_SUBTYPE_PLURAL_MODIFIER.fullmatch(
            oracle_line.strip()
        )
        subtype_plural = match is not None
    if match is None or bool(match.group("until")) is not until_end_of_turn:
        return None
    qualifier = (
        _singular_creature_subtype(match.group("plural"))
        if subtype_plural
        else (match.group("qualifier") or "").casefold()
    )
    if subtype_plural and qualifier is None:
        return None
    fields: dict[str, Any] = {
        "zones": ("battlefield",),
        "types_all": ("creature",),
    }
    if qualifier == "artifact":
        fields["types_all"] = ("artifact", "creature")
    elif qualifier == "legendary":
        fields["supertypes_all"] = ("legendary",)
    elif qualifier in _COLOR_SYMBOLS:
        fields["colors_all"] = (_COLOR_SYMBOLS[qualifier],)
    elif qualifier:
        subtype = canonical_creature_subtype(qualifier)
        if subtype is None or _STATEFUL_CREATURE_QUALIFIER.fullmatch(qualifier):
            return None
        fields["subtypes_all"] = (subtype,)
    return (
        ObjectQuerySpec(**fields),
        int(match.group("power")),
        int(match.group("toughness")),
        bool(match.group("other")),
    )


def fixed_power_toughness_battlefield_query(
    subject: str,
    oracle_line: str,
) -> tuple[str, ObjectQuerySpec, bool] | None:
    """Apply the closed layer-7c subject policy before parsing its query."""

    existing = controlled_creature_fixed_modifier(
        oracle_line,
        until_end_of_turn=False,
    )
    stateful = _STATE_QUALIFIED_BATTLEFIELD_SUBJECT.fullmatch(subject)
    policy_subject = (
        ("Other " if stateful.group("other") else "")
        + stateful.group("subject")
        if stateful is not None
        else subject
    )
    controlled = (
        controlled_creature_fixed_modifier(
            f"{policy_subject} get +0/+0.",
            until_end_of_turn=False,
        )
        if stateful is not None
        else existing
    )
    global_subject = _GLOBAL_SUBJECT.fullmatch(policy_subject)
    if controlled is None and not (
        _CONTROLLED_MULTICOLORED_CREATURES.fullmatch(policy_subject)
        or _OPPONENT_CONTROLLED_CREATURES.fullmatch(policy_subject)
        or (
            global_subject is not None
            and global_subject.group("subject").casefold() == "creatures"
        )
        or _GLOBAL_SUBTYPE_CREATURES.fullmatch(policy_subject)
        or _GLOBAL_PLURAL_SUBJECT.fullmatch(policy_subject)
    ):
        return None
    return fixed_battlefield_query_subject(subject)


def _self_subject_pattern(source_name: str) -> str:
    source = SourceReferenceSpec(source_name).regex_pattern
    return rf"(?:This creature|This permanent|This token|{source})"


def _fixed_condition_amount(value: str) -> int | None:
    normalized = value.strip().casefold()
    if normalized.isdigit():
        return int(normalized)
    return _CONDITION_NUMBER_WORDS.get(normalized)


def _fixed_condition_object_query(
    subject: str,
    quality: str,
) -> ObjectQuerySpec | None:
    """Compile one positive public characteristic phrase through layer 5."""

    fields: dict[str, Any] = {"zones": ("battlefield",)}
    normalized_subject = subject.strip().casefold()
    normalized_quality = quality.strip().casefold()
    semantic_quality = re.sub(r"^(?:a|an)\s+", "", normalized_quality)
    if normalized_subject not in {
        "artifact",
        "creature",
        "enchantment",
        "land",
        "permanent",
    }:
        return None
    if normalized_subject != "permanent":
        fields["types_all"] = (normalized_subject,)
    if normalized_quality in {"", "a", "an"}:
        return ObjectQuerySpec(**fields)
    if semantic_quality in _CARD_TYPE_WORDS:
        fields["types_all"] = tuple(
            sorted({*fields.get("types_all", ()), semantic_quality})
        )
        return ObjectQuerySpec(**fields)
    if semantic_quality == "equipment":
        fields.update(types_all=("artifact",), subtypes_all=("equipment",))
        return ObjectQuerySpec(**fields)
    if semantic_quality in _COLOR_SYMBOLS:
        fields["colors_all"] = (_COLOR_SYMBOLS[semantic_quality],)
        return ObjectQuerySpec(**fields)
    if semantic_quality == "legendary":
        fields["supertypes_all"] = ("legendary",)
        return ObjectQuerySpec(**fields)
    if semantic_quality.startswith("basic "):
        subtype = semantic_quality.removeprefix("basic ")
        if subtype not in {"plains", "island", "swamp", "mountain", "forest"}:
            return None
        fields.update(
            types_all=("land",),
            supertypes_all=("basic",),
            subtypes_all=(subtype,),
        )
        return ObjectQuerySpec(**fields)
    subtype_terms = re.fullmatch(
        r"(?:a|an) (?P<first>[A-Za-z][A-Za-z'-]*)"
        r"(?: or (?:a|an) (?P<second>[A-Za-z][A-Za-z'-]*))?",
        quality.strip(),
        re.IGNORECASE,
    )
    if subtype_terms is None:
        return None
    raw_subtypes = tuple(filter(None, subtype_terms.groups()))
    subtypes = tuple(
        subtype
        for raw in raw_subtypes
        if (subtype := canonical_creature_subtype(raw)) is not None
    )
    if not subtypes or len(subtypes) != len(raw_subtypes):
        return None
    fields["subtypes_any"] = subtypes
    return ObjectQuerySpec(**fields)


def _fixed_public_query_count_condition(
    text: str,
) -> FixedPublicStateConditionSpec | None:
    normalized = text.strip().rstrip(".")
    match = re.fullmatch(
        r"(?P<relation>you control|an opponent controls) "
        r"(?P<count>a|an|one|two|three|four|five|six|seven|eight|nine|ten|[0-9]+)"
        r"(?P<minimum> or more)? "
        r"(?P<quality>black |blue |green |red |white |basic )?"
        r"(?P<subject>artifacts?|creatures?|enchantments?|lands?|"
        r"permanents?|Equipment)",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return None
    amount = _fixed_condition_amount(match.group("count"))
    if amount is None or amount <= 0:
        return None
    if match.group("minimum") is None and match.group("count").casefold() not in {
        "a",
        "an",
    }:
        return None
    opponent_relation = match.group("relation").casefold() == (
        "an opponent controls"
    )
    if opponent_relation and amount != 1:
        # The shared opponent-zone quantity is aggregate. Only existence has
        # the same meaning as Oracle's per-opponent condition.
        return None
    raw_subject = match.group("subject").casefold()
    if raw_subject == "equipment":
        query = ObjectQuerySpec(
            zones=("battlefield",),
            types_all=("artifact",),
            subtypes_all=("equipment",),
        )
    else:
        query = _fixed_condition_object_query(
            raw_subject.removesuffix("s"),
            (match.group("quality") or "").strip(),
        )
    if query is None:
        return None
    return FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST,
        amount=amount,
        quantity=CharacteristicQuantitySpec(
            scope=(
                CharacteristicQuantityScope.CONTROLLER_ZONE
                if not opponent_relation
                else CharacteristicQuantityScope.OPPONENT_ZONES
            ),
            query=query,
        ),
        schema_version=2,
    )


def _legacy_public_state_condition(
    normalized: str,
) -> FixedPublicStateConditionSpec | None:
    lower = normalized.casefold()
    if lower in {"your turn", "it's your turn"}:
        return FixedPublicStateConditionSpec(
            FixedPublicStateConditionKind.CONTROLLER_TURN
        )
    if lower == "turns other than yours":
        return FixedPublicStateConditionSpec(
            FixedPublicStateConditionKind.OTHER_TURN
        )
    if lower == "there are seven or more cards in your graveyard":
        return FixedPublicStateConditionSpec(
            FixedPublicStateConditionKind.CONTROLLER_GRAVEYARD_CARD_COUNT_AT_LEAST,
            amount=7,
        )
    hand = re.fullmatch(
        r"you have (?P<count>no|one|[0-9]+) "
        r"(?:cards? in hand|or fewer cards in hand)",
        lower,
    )
    if hand is not None:
        amount = _fixed_condition_amount(hand.group("count"))
        assert amount is not None
        return FixedPublicStateConditionSpec(
            FixedPublicStateConditionKind.CONTROLLER_HAND_COUNT_AT_MOST,
            amount=amount,
        )
    life = re.fullmatch(
        r"you have (?P<amount>[0-9]+) or (?P<bound>more|less) life",
        lower,
    )
    if life is not None:
        return FixedPublicStateConditionSpec(
            (
                FixedPublicStateConditionKind.CONTROLLER_LIFE_AT_LEAST
                if life.group("bound") == "more"
                else FixedPublicStateConditionKind.CONTROLLER_LIFE_AT_MOST
            ),
            amount=int(life.group("amount")),
        )
    opponent_life = re.fullmatch(
        r"an opponent has (?P<amount>[0-9]+) or less life",
        lower,
    )
    if opponent_life is None:
        return None
    return FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.OPPONENT_LIFE_AT_MOST,
        amount=int(opponent_life.group("amount")),
    )


def _object_public_state_condition(
    normalized: str,
    *,
    source_name: str,
) -> FixedPublicStateConditionSpec | None:
    subject = _self_subject_pattern(source_name)
    source_state = re.fullmatch(
        rf"(?:it|{subject}) is (?P<state>attacking|blocking|enchanted|"
        r"equipped|modified|monstrous|tapped|untapped)",
        normalized,
        re.IGNORECASE,
    )
    if source_state is not None:
        state = source_state.group("state").casefold()
        state_fields = (
            {"tapped": state == "tapped"}
            if state in {"tapped", "untapped"}
            else {state: True}
        )
        return FixedPublicStateConditionSpec(
            FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY,
            predicate=ObjectQuerySpec(
                zones=("battlefield",),
                state_predicate=PermanentStatePredicateSpec(**state_fields),
            ),
            schema_version=2,
        )
    attached_state = re.fullmatch(
        r"(?P<relation>enchanted|equipped) "
        r"(?P<subject>creature|permanent|land) is (?P<quality>.+)",
        normalized,
        re.IGNORECASE,
    )
    if attached_state is None:
        return _fixed_public_query_count_condition(normalized)
    predicate = _fixed_condition_object_query(
        attached_state.group("subject"),
        attached_state.group("quality"),
    )
    if predicate is None:
        return None
    return FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.ATTACHED_MATCHES_QUERY,
        predicate=predicate,
        schema_version=2,
    )


def _source_history_public_state_condition(
    normalized: str,
    *,
    source_name: str,
) -> FixedPublicStateConditionSpec | None:
    subject = _self_subject_pattern(source_name)
    if re.fullmatch(
        rf"(?:it|{subject}) entered this turn",
        normalized,
        re.IGNORECASE,
    ) is not None:
        return FixedPublicStateConditionSpec(
            FixedPublicStateConditionKind.SOURCE_ENTERED_THIS_TURN
        )
    counter = re.fullmatch(
        rf"(?:it|{subject}) has (?P<count>a|an|one|two|three|four|five|six|"
        r"seven|eight|nine|ten|[0-9]+)(?: or more)? "
        r"(?P<counter>[A-Za-z0-9+/-]+) counters? on it",
        normalized,
        re.IGNORECASE,
    )
    if counter is None:
        return None
    amount = _fixed_condition_amount(counter.group("count"))
    if amount is None or amount <= 0:
        return None
    return FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.SOURCE_COUNTER_AT_LEAST,
        amount=amount,
        counter_name=counter.group("counter"),
    )


def _fixed_public_state_condition(
    text: str,
    *,
    source_name: str,
) -> FixedPublicStateConditionSpec | None:
    normalized = text.strip().rstrip(".")
    legacy = _legacy_public_state_condition(normalized)
    if legacy is not None:
        return legacy
    public_object = _object_public_state_condition(
        normalized,
        source_name=source_name,
    )
    if public_object is not None:
        return public_object
    return _source_history_public_state_condition(
        normalized,
        source_name=source_name,
    )


def fixed_public_state_parts(
    oracle_line: str,
    *,
    source_name: str,
) -> tuple[FixedPublicStateConditionSpec, str] | None:
    text = _TRAILING_REMINDER.sub("", oracle_line.strip()).strip()
    ability_word = re.fullmatch(
        r"[A-Z][A-Za-z' ]{0,80} — (?P<body>.+)",
        text,
    )
    if ability_word is not None:
        text = ability_word.group("body").strip()
    during = re.fullmatch(
        r"During (?P<condition>your turn|turns other than yours), "
        r"(?P<body>.+)",
        text,
        re.IGNORECASE,
    )
    if during is not None:
        condition = _fixed_public_state_condition(
            during.group("condition"),
            source_name=source_name,
        )
        return (condition, during.group("body")) if condition else None
    prefix = re.fullmatch(
        r"As long as (?P<condition>.+?), (?P<body>.+)",
        text,
        re.IGNORECASE,
    )
    if prefix is not None:
        condition = _fixed_public_state_condition(
            prefix.group("condition"),
            source_name=source_name,
        )
        return (condition, prefix.group("body")) if condition else None
    marker = text.casefold().rfind(" as long as ")
    if marker <= 0:
        return None
    condition = _fixed_public_state_condition(
        text[marker + len(" as long as ") :],
        source_name=source_name,
    )
    return (condition, text[:marker]) if condition else None


__all__ = [
    "controlled_creature_fixed_modifier",
    "fixed_battlefield_query_subject",
    "fixed_power_toughness_battlefield_query",
    "fixed_public_state_parts",
]
