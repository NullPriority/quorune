from __future__ import annotations

import re
from typing import Any, Mapping

from ..object_predicate import ObjectQuerySpec
from ..ability_fragments import (
    ConditionalKeywordSpec,
    DynamicPowerToughnessSpec,
    ToxicSpec,
    ability_fragment_to_dict,
    parse_protection_line,
)
from ..characteristic_fragments import (
    CharacteristicCountKind,
    PowerToughnessCalculation,
)
from ..keyword_abilities import (
    FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES,
    FIXED_CHARACTERISTIC_KEYWORDS,
)
from .creature_subtypes import canonical_creature_subtype
from ..rules.source_references import SourceReferenceSpec
from ..trigger_participation import WardSpec
from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID,
    FixedPublicStateConditionKind,
    FixedPublicStateConditionSpec,
)


_BASIC_LAND_TYPE_ADDITION = re.compile(
    r"^Each land is (?:a|an) "
    r"(?P<subtype>Plains|Island|Swamp|Mountain|Forest) "
    r"in addition to its other land types\.?$",
    re.IGNORECASE,
)
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
    r"^(?:attacking|blocking|enchanted|equipped|tapped|untapped)$"
)
_ATTACHED_SUBJECT = r"(?:Enchanted creature|Equipped creature|Fortified land)"
_ATTACHED_FIXED_CHARACTERISTICS = re.compile(
    rf"^(?P<subject>{_ATTACHED_SUBJECT}) (?P<body>.+?)\.?$",
    re.IGNORECASE,
)
_ATTACHED_FIXED_PT = re.compile(
    r"^gets (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+)"
    r"(?: and has (?P<abilities>.+))?$",
    re.IGNORECASE,
)
_ATTACHED_HAS_OR_LOSES = re.compile(
    r"^(?P<verb>has|loses) (?P<abilities>.+)$",
    re.IGNORECASE,
)
_ATTACHED_ADDED_TYPE = re.compile(
    r"^is (?:a|an) (?P<type>[A-Z][A-Za-z'-]*) "
    r"in addition to its other types$",
)
_ATTACHED_SUPPORTED_ABILITIES = frozenset(
    {
        "deathtouch",
        "defender",
        "double strike",
        "first strike",
        "flash",
        "flying",
        "haste",
        "hexproof",
        "indestructible",
        "infect",
        "lifelink",
        "menace",
        "reach",
        "shadow",
        "shroud",
        "trample",
        "vigilance",
        "wither",
    }
)
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
_CONTROLLED_KEYWORD_GRANT = re.compile(
    r"^(?P<other>Other )?"
    r"(?:(?P<quality>Artifact|Black|Blue|Colorless|Green|Land|Legendary|"
    r"Nontoken|Red|Token|White) )?"
    r"(?P<subject>creatures|permanents|artifacts|lands) you control have "
    r"(?P<abilities>.+?)\.?$",
    re.IGNORECASE,
)
_CONTROLLED_CREATURE_TOKEN_KEYWORD_GRANT = re.compile(
    r"^(?P<other>Other )?Creature tokens you control have "
    r"(?P<abilities>.+?)\.?$",
    re.IGNORECASE,
)
_CONTROLLED_SUBTYPE_KEYWORD_GRANT = re.compile(
    r"^(?P<other>Other )?(?P<subtype>[A-Z][A-Za-z'-]*(?: [A-Z][A-Za-z'-]*)?) "
    r"creatures you control have (?P<abilities>.+?)\.?$"
)
_CONTROLLED_PLURAL_KEYWORD_GRANT = re.compile(
    r"^(?P<other>Other )?(?P<plural>[A-Z][A-Za-z'-]*) you control have "
    r"(?P<abilities>.+?)\.?$"
)
_GLOBAL_KEYWORD_GRANT = re.compile(
    r"^(?:All )?(?P<subject>creatures|artifacts|lands) have "
    r"(?P<abilities>.+?)\.?$",
    re.IGNORECASE,
)
_GLOBAL_SUBTYPE_KEYWORD_GRANT = re.compile(
    r"^(?:All )?(?P<subtype>[A-Z][A-Za-z'-]*(?: [A-Z][A-Za-z'-]*)?) "
    r"creatures have (?P<abilities>.+?)\.?$"
)
_GLOBAL_PLURAL_KEYWORD_GRANT = re.compile(
    r"^All (?P<plural>[A-Z][A-Za-z'-]*) have (?P<abilities>.+?)\.?$"
)
_TRAILING_REMINDER = re.compile(r"\s+\([^()]*\)\.?$")
_FIXED_QUERY_CHARACTERISTIC_GRANT = re.compile(
    r"^(?P<subject>.+ you control) get "
    r"(?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) and have "
    r"(?P<abilities>.+?)\.?$",
    re.IGNORECASE,
)
_COLOR_SYMBOLS = {
    "black": "B",
    "blue": "U",
    "green": "G",
    "red": "R",
    "white": "W",
}


def _singular_creature_subtype(plural: str) -> str | None:
    value = plural.casefold()
    if value in _IRREGULAR_CREATURE_PLURALS:
        candidate = _IRREGULAR_CREATURE_PLURALS[value]
        return canonical_creature_subtype(candidate)
    if value.endswith("s") and not value.endswith("ss") and len(value) > 2:
        return canonical_creature_subtype(value[:-1])
    return None


def controlled_creature_fixed_modifier(
    oracle_line: str,
    *,
    until_end_of_turn: bool,
) -> tuple[ObjectQuerySpec, int, int, bool] | None:
    """Parse one closed fixed modifier over controlled creatures.

    Conditional, combat-state, token, equipped, enchanted, commander, and
    negative-color predicates remain residual rather than being approximated.
    """

    text = oracle_line.strip()
    match = _CONTROLLED_CREATURE_MODIFIER.fullmatch(text)
    subtype_plural = False
    if match is None:
        match = _CONTROLLED_SUBTYPE_PLURAL_MODIFIER.fullmatch(text)
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
    elif qualifier in {"white", "blue", "black", "red", "green"}:
        fields["colors_all"] = (
            {
                "white": "W",
                "blue": "U",
                "black": "B",
                "red": "R",
                "green": "G",
            }[qualifier],
        )
    elif qualifier:
        # Capitalization is not semantic.  Only the pinned CR 205.3m
        # creature-type vocabulary may enter the subtype predicate.  The
        # grammar deliberately leaves state, token, snow, commander,
        # negative, compound, and other unsupported qualities residual.
        subtype = canonical_creature_subtype(qualifier)
        if subtype is None or _STATEFUL_CREATURE_QUALIFIER.fullmatch(
            qualifier
        ):
            return None
        fields["subtypes_all"] = (subtype,)
    return (
        ObjectQuerySpec(**fields),
        int(match.group("power")),
        int(match.group("toughness")),
        bool(match.group("other")),
    )


def fixed_power_toughness_anthem_handler(
    oracle_line: str,
) -> tuple[str, Mapping[str, Any], str] | None:
    parsed = controlled_creature_fixed_modifier(
        oracle_line, until_end_of_turn=False
    )
    if parsed is None:
        return None
    predicate, power, toughness, exclude_source = parsed
    return (
        "continuous-fixed-query-anthem-v2",
        {
            "handler_id": "continuous.anthem.fixed-query.v2",
            "schema_version": 2,
            "event": "characteristics.evaluate",
            "condition": {
                "target_controller": "source_controller",
                "predicate": predicate.to_dict(),
                "exclude_source": exclude_source,
            },
            "modifier": {"power": power, "toughness": toughness},
        },
        "continuous.power_toughness.fixed_anthem",
    )


def fixed_query_keyword_grant_handler(
    oracle_line: str,
) -> tuple[str, Mapping[str, Any], tuple[str, ...]] | None:
    """Lower one closed live-set combat-keyword grant.

    The grammar represents only fixed battlefield sets whose controller,
    card type, color, supertype, token status, or pinned creature subtype can
    be evaluated through ``ObjectQuerySpec``.  Conditional, opponent-relative,
    attacking/blocking, modified, counter-qualified, multicolored, and chosen
    sets remain residual.  Every accepted keyword declares its exact trusted
    combat, damage, destruction, or targeting consumer capability.
    """

    text = _TRAILING_REMINDER.sub("", oracle_line.strip()).strip()
    relation = "source_controller"
    exclude_source = False
    fields: dict[str, Any] = {"zones": ("battlefield",)}
    match = _CONTROLLED_CREATURE_TOKEN_KEYWORD_GRANT.fullmatch(text)
    if match is not None:
        exclude_source = bool(match.group("other"))
        fields.update(types_all=("creature",), token=True)
        abilities_text = match.group("abilities")
    elif (match := _CONTROLLED_KEYWORD_GRANT.fullmatch(text)) is not None:
        exclude_source = bool(match.group("other"))
        subject = match.group("subject").casefold()
        quality = (match.group("quality") or "").casefold()
        if quality and subject != "creatures":
            return None
        if subject == "creatures":
            fields["types_all"] = ("creature",)
        elif subject == "artifacts":
            fields["types_all"] = ("artifact",)
        elif subject == "lands":
            fields["types_all"] = ("land",)
        if quality in {"artifact", "land"}:
            fields["types_all"] = (quality, "creature")
        elif quality in _COLOR_SYMBOLS:
            fields["types_all"] = ("creature",)
            fields["colors_all"] = (_COLOR_SYMBOLS[quality],)
        elif quality == "colorless":
            fields["types_all"] = ("creature",)
            fields["colorless"] = True
        elif quality == "legendary":
            fields["types_all"] = ("creature",)
            fields["supertypes_all"] = ("legendary",)
        elif quality in {"nontoken", "token"}:
            fields["types_all"] = ("creature",)
            fields["token"] = quality == "token"
        abilities_text = match.group("abilities")
    else:
        match = _CONTROLLED_SUBTYPE_KEYWORD_GRANT.fullmatch(text)
        if match is not None:
            subtype = canonical_creature_subtype(match.group("subtype"))
            if subtype is None:
                return None
            exclude_source = bool(match.group("other"))
            fields.update(
                types_all=("creature",),
                subtypes_all=(subtype,),
            )
            abilities_text = match.group("abilities")
        else:
            match = _CONTROLLED_PLURAL_KEYWORD_GRANT.fullmatch(text)
            if match is not None:
                plural = match.group("plural")
                exclude_source = bool(match.group("other"))
                if plural.casefold() == "vehicles":
                    fields.update(
                        types_all=("artifact",),
                        subtypes_all=("vehicle",),
                    )
                else:
                    subtype = _singular_creature_subtype(plural)
                    if subtype is None:
                        return None
                    fields.update(
                        types_all=("creature",),
                        subtypes_all=(subtype,),
                    )
                abilities_text = match.group("abilities")
            else:
                relation = "any"
                match = _GLOBAL_KEYWORD_GRANT.fullmatch(text)
                if match is not None:
                    subject = match.group("subject").casefold()
                    fields["types_all"] = (
                        ("creature",)
                        if subject == "creatures"
                        else (subject.removesuffix("s"),)
                    )
                    abilities_text = match.group("abilities")
                else:
                    match = _GLOBAL_SUBTYPE_KEYWORD_GRANT.fullmatch(text)
                    if match is not None:
                        subtype = canonical_creature_subtype(
                            match.group("subtype")
                        )
                    else:
                        match = _GLOBAL_PLURAL_KEYWORD_GRANT.fullmatch(text)
                        subtype = (
                            _singular_creature_subtype(match.group("plural"))
                            if match is not None
                            else None
                        )
                    if match is None or subtype is None:
                        return None
                    fields.update(
                        types_all=("creature",),
                        subtypes_all=(subtype,),
                    )
                    abilities_text = match.group("abilities")

    normalized = re.sub(
        r",?\s+and\s+", ",", abilities_text.strip(), flags=re.IGNORECASE
    )
    abilities = tuple(
        value.strip().title()
        for value in normalized.rstrip(".").split(",")
        if value.strip()
    )
    if not abilities or len(set(abilities)) != len(abilities):
        return None
    predicate = ObjectQuerySpec(**fields)
    if any(
        ability not in FIXED_CHARACTERISTIC_KEYWORDS for ability in abilities
    ):
        return None
    capabilities = {
        "continuous.ability.fixed_query_keyword_grant",
        *(
            capability
            for ability in abilities
            for capability in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[ability]
        ),
    }
    return (
        "continuous-fixed-query-keyword-grant-v2",
        {
            "handler_id": "continuous.ability.fixed-query-keyword-grant.v1",
            "schema_version": 1,
            "event": "characteristics.evaluate",
            "condition": {
                "target_controller": relation,
                "predicate": predicate.to_dict(),
                "exclude_source": exclude_source,
            },
            "modifier": {"add_abilities": list(abilities)},
        },
        tuple(sorted(capabilities)),
    )


def fixed_query_characteristic_grant_handler(
    oracle_line: str,
) -> tuple[str, Mapping[str, Any], tuple[str, ...]] | None:
    """Lower one fixed query that grants P/T and represented keywords.

    Both halves must independently satisfy the existing live-set grammars and
    resolve to the same canonical query.  Compound wording therefore cannot
    widen either the layer-6 or layer-7c boundary.
    """

    text = _TRAILING_REMINDER.sub("", oracle_line.strip()).strip()
    match = _FIXED_QUERY_CHARACTERISTIC_GRANT.fullmatch(text)
    if match is None:
        return None
    subject = match.group("subject")
    anthem = fixed_power_toughness_anthem_handler(
        f"{subject} get {match.group('power')}/{match.group('toughness')}."
    )
    keywords = fixed_query_keyword_grant_handler(
        f"{subject} have {match.group('abilities')}."
    )
    if anthem is None or keywords is None:
        return None
    anthem_condition = anthem[1]["condition"]
    keyword_condition = keywords[1]["condition"]
    if anthem_condition != keyword_condition:
        return None
    return (
        "continuous-fixed-query-characteristic-grant-v1",
        {
            "handler_id": (
                "continuous.characteristics.fixed-query-grant.v1"
            ),
            "schema_version": 1,
            "event": "characteristics.evaluate",
            "condition": dict(keyword_condition),
            "modifier": {
                "add_abilities": list(
                    keywords[1]["modifier"]["add_abilities"]
                ),
                "power": int(anthem[1]["modifier"]["power"]),
                "toughness": int(anthem[1]["modifier"]["toughness"]),
            },
        },
        tuple(sorted({anthem[2], *keywords[2]})),
    )


def _self_subject_pattern(source_name: str) -> str:
    source = SourceReferenceSpec(source_name).regex_pattern
    return rf"(?:This creature|This permanent|This token|{source})"


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


def _fixed_condition_amount(value: str) -> int | None:
    normalized = value.strip().casefold()
    if normalized.isdigit():
        return int(normalized)
    return _CONDITION_NUMBER_WORDS.get(normalized)


def _fixed_public_state_condition(
    text: str,
    *,
    source_name: str,
) -> FixedPublicStateConditionSpec | None:
    normalized = text.strip().rstrip(".")
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
    if opponent_life is not None:
        return FixedPublicStateConditionSpec(
            FixedPublicStateConditionKind.OPPONENT_LIFE_AT_MOST,
            amount=int(opponent_life.group("amount")),
        )

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


def _fixed_public_state_parts(
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


def conditional_self_keyword_handler(
    oracle_line: str,
    *,
    source_name: str,
) -> tuple[str, Mapping[str, Any], str] | None:
    pattern = re.compile(
        rf"^{_self_subject_pattern(source_name)} has haste as long as an "
        r"opponent has (?P<life>\d+) or less life\.?$",
        re.IGNORECASE,
    )
    match = pattern.fullmatch(oracle_line.strip())
    if match is None:
        return None
    fragment = ConditionalKeywordSpec(
        keyword="Haste",
        opponent_life_at_most=int(match.group("life")),
    )
    return (
        "continuous-self-conditional-keyword-v1",
        {
            "handler_id": "ability.static.conditional-keyword.v1",
            "schema_version": 1,
            "event": "continuous",
            "fragment": ability_fragment_to_dict(fragment),
        },
        "continuous.characteristics.conditional_keyword",
    )


def dynamic_self_power_toughness_handler(
    oracle_line: str,
    *,
    source_name: str,
) -> tuple[str, Mapping[str, Any], str] | None:
    subject = _self_subject_pattern(source_name)
    per_object = re.compile(
        rf"^{subject} gets \+1/\+1 for each (?P<object>artifact you "
        r"control|creature card in your graveyard)\.?$",
        re.IGNORECASE,
    ).fullmatch(oracle_line.strip())
    if per_object is not None:
        count_kind = (
            CharacteristicCountKind.CONTROLLER_BATTLEFIELD_ARTIFACTS
            if per_object.group("object").casefold().startswith("artifact")
            else CharacteristicCountKind.OWNER_GRAVEYARD_CREATURE_CARDS
        )
        fragment = DynamicPowerToughnessSpec(
            count_kind=count_kind,
            calculation=PowerToughnessCalculation.PER_MATCHING_OBJECT,
            power=1,
            toughness=1,
        )
    else:
        threshold = re.compile(
            rf"^{subject} gets \+2/\+2 as long as there are three or more "
            r"land cards in your graveyard\.?$",
            re.IGNORECASE,
        ).fullmatch(oracle_line.strip())
        if threshold is None:
            return None
        fragment = DynamicPowerToughnessSpec(
            count_kind=CharacteristicCountKind.OWNER_GRAVEYARD_LAND_CARDS,
            calculation=PowerToughnessCalculation.FIXED_IF_THRESHOLD,
            power=2,
            toughness=2,
            minimum_count=3,
        )
    return (
        "continuous-self-dynamic-power-toughness-v1",
        {
            "handler_id": "ability.static.dynamic-power-toughness.v1",
            "schema_version": 1,
            "event": "continuous",
            "fragment": ability_fragment_to_dict(fragment),
        },
        "continuous.characteristics.dynamic_power_toughness",
    )


def _attached_abilities(value: str) -> tuple[str, ...] | None:
    normalized = re.sub(r",?\s+and\s+", ",", value.strip())
    abilities = tuple(
        part.strip().casefold()
        for part in normalized.split(",")
        if part.strip()
    )
    if (
        not abilities
        or len(set(abilities)) != len(abilities)
        or any(
            ability not in _ATTACHED_SUPPORTED_ABILITIES
            and re.fullmatch(r"toxic [1-9]\d*", ability) is None
            for ability in abilities
        )
    ):
        return None
    return tuple(ability.title() for ability in abilities)


def _toxic_ability_fragments(
    abilities: tuple[str, ...],
) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        ability_fragment_to_dict(
            ToxicSpec(value=int(match.group("value")))
        )
        for ability in abilities
        if (
            match := re.fullmatch(
                r"Toxic (?P<value>[1-9]\d*)",
                ability,
                re.IGNORECASE,
            )
        )
        is not None
    )


def _attached_granted_abilities(
    value: str,
) -> tuple[tuple[str, ...], tuple[Mapping[str, Any], ...]] | None:
    abilities = _attached_abilities(value)
    if abilities is not None:
        return abilities, _toxic_ability_fragments(abilities)
    protection = parse_protection_line(value)
    if protection is not None:
        return (
            ("Protection",),
            tuple(
                ability_fragment_to_dict(fragment)
                for fragment in protection
            ),
        )
    ward = re.fullmatch(
        r"ward \{(?P<generic>[1-9]\d*)\}",
        value.strip(),
        re.IGNORECASE,
    )
    if ward is None:
        return None
    return (
        (f"Ward {{{int(ward.group('generic'))}}}",),
        (
            ability_fragment_to_dict(
                WardSpec(generic_cost=int(ward.group("generic")))
            ),
        ),
    )


def _attached_ability_capabilities(
    abilities: tuple[str, ...],
) -> set[str]:
    capabilities: set[str] = set()
    for ability in abilities:
        if ability == "Protection":
            capabilities.add("protection.typed.debt")
        elif ability.startswith("Toxic "):
            capabilities.add("damage.result.toxic")
        elif ability.startswith("Ward {"):
            capabilities.add("trigger.keyword.ward.fixed_generic")
        else:
            capabilities.update(
                FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES.get(ability, ())
            )
    return capabilities


def attached_fixed_characteristics_handler(
    oracle_line: str,
) -> tuple[str, Mapping[str, Any], tuple[str, ...]] | None:
    """Lower one closed attached-object fixed-characteristic sentence.

    Dynamic values, conditions, combat restrictions, quoted rules text, and
    mechanics outside the reviewed keyword vocabulary remain residual.
    """

    text = _TRAILING_REMINDER.sub("", oracle_line.strip()).strip()
    match = _ATTACHED_FIXED_CHARACTERISTICS.fullmatch(text)
    if match is None:
        return None
    subject_types_all = (
        ["land"]
        if match.group("subject").casefold() == "fortified land"
        else ["creature"]
    )
    body = match.group("body")
    type_operations: list[dict[str, Any]] = []
    add_abilities: tuple[str, ...] = ()
    remove_abilities: tuple[str, ...] = ()
    add_ability_fragments: tuple[Mapping[str, Any], ...] = ()
    power = 0
    toughness = 0

    pt_match = _ATTACHED_FIXED_PT.fullmatch(body)
    ability_match = _ATTACHED_HAS_OR_LOSES.fullmatch(body)
    type_match = _ATTACHED_ADDED_TYPE.fullmatch(body)
    if pt_match is not None:
        power = int(pt_match.group("power"))
        toughness = int(pt_match.group("toughness"))
        if pt_match.group("abilities"):
            granted = _attached_granted_abilities(
                pt_match.group("abilities")
            )
            if granted is None:
                return None
            add_abilities, add_ability_fragments = granted
    elif ability_match is not None:
        parsed = _attached_abilities(ability_match.group("abilities"))
        if ability_match.group("verb").casefold() == "has":
            granted = _attached_granted_abilities(
                ability_match.group("abilities")
            )
            if granted is None:
                return None
            add_abilities, add_ability_fragments = granted
        else:
            if parsed is None:
                return None
            if _toxic_ability_fragments(parsed):
                # Removing a typed granted/printed ability requires a closed
                # fragment-removal descriptor, which this handler does not yet
                # own. Do not leave the executable fragment behind.
                return None
            remove_abilities = parsed
    elif type_match is not None:
        type_word = type_match.group("type")
        type_operations.append(
            {
                "op": "add_types",
                "field": (
                    "card_types"
                    if type_word.casefold() in _CARD_TYPE_WORDS
                    else "subtypes"
                ),
                "values": [type_word],
            }
        )
    else:
        return None

    if not (
        type_operations
        or add_abilities
        or remove_abilities
        or add_ability_fragments
        or power
        or toughness
    ):
        return None
    return (
        "continuous-attached-fixed-characteristics-v1",
        {
            "handler_id": "continuous.attached.fixed-characteristics.v1",
            "schema_version": 1,
            "event": "characteristics.evaluate",
            "condition": {
                "relation": "source_attached_object",
                "types_all": subject_types_all,
            },
            "modifier": {
                "type_operations": type_operations,
                "add_abilities": list(add_abilities),
                "remove_abilities": list(remove_abilities),
                "add_rules_text": [],
                "add_ability_fragments": list(
                    add_ability_fragments
                ),
                "power": power,
                "toughness": toughness,
            },
        },
        tuple(
            sorted(
                {
                    "continuous.attached.fixed_characteristics",
                    *_attached_ability_capabilities(
                        (*add_abilities, *remove_abilities)
                    ),
                }
            )
        ),
    )


def _conditional_target(
    body: str,
    *,
    source_name: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any], tuple[str, ...]] | None:
    """Compile one fixed characteristic body without its state condition."""

    normalized = _TRAILING_REMINDER.sub("", body.strip()).strip()
    subject = _self_subject_pattern(source_name)
    self_pt = re.fullmatch(
        rf"{subject} gets (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+)"
        r"(?: and has (?P<abilities>.+))?\.?",
        normalized,
        re.IGNORECASE,
    )
    self_keyword = re.fullmatch(
        rf"{subject} has (?P<abilities>.+?)\.?",
        normalized,
        re.IGNORECASE,
    )
    if self_pt is not None or self_keyword is not None:
        abilities_text = (
            self_pt.group("abilities")
            if self_pt is not None
            else self_keyword.group("abilities")
        )
        abilities: tuple[str, ...] = ()
        capabilities: set[str] = set()
        if abilities_text:
            normalized_abilities = re.sub(
                r",?\s+and\s+",
                ",",
                abilities_text.strip(),
                flags=re.IGNORECASE,
            )
            abilities = tuple(
                value.strip().title()
                for value in normalized_abilities.rstrip(".").split(",")
                if value.strip()
            )
            if (
                not abilities
                or len(set(abilities)) != len(abilities)
                or any(
                    ability not in FIXED_CHARACTERISTIC_KEYWORDS
                    for ability in abilities
                )
            ):
                return None
            capabilities.update(
                capability
                for ability in abilities
                for capability in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[
                    ability
                ]
            )
            capabilities.add(
                "continuous.ability.fixed_query_keyword_grant"
            )
        power = int(self_pt.group("power")) if self_pt is not None else 0
        toughness = (
            int(self_pt.group("toughness")) if self_pt is not None else 0
        )
        if power or toughness:
            capabilities.add("continuous.power_toughness.fixed_anthem")
        return (
            {
                "kind": "source",
                "target_controller": None,
                "predicate": None,
                "exclude_source": False,
                "types_all": [],
            },
            {
                "add_abilities": list(abilities),
                "power": power,
                "toughness": toughness,
            },
            tuple(sorted(capabilities)),
        )

    for compiler in (
        fixed_query_characteristic_grant_handler,
        fixed_query_keyword_grant_handler,
        fixed_power_toughness_anthem_handler,
    ):
        compiled = compiler(normalized)
        if compiled is None:
            continue
        descriptor = compiled[1]
        condition = descriptor["condition"]
        modifier = descriptor["modifier"]
        return (
            {
                "kind": "fixed_query",
                "target_controller": condition["target_controller"],
                "predicate": condition["predicate"],
                "exclude_source": condition["exclude_source"],
                "types_all": [],
            },
            {
                "add_abilities": list(
                    modifier.get("add_abilities", [])
                ),
                "power": int(modifier.get("power", 0)),
                "toughness": int(modifier.get("toughness", 0)),
            },
            (
                (compiled[2],)
                if isinstance(compiled[2], str)
                else tuple(compiled[2])
            ),
        )

    attached = attached_fixed_characteristics_handler(normalized)
    if attached is None:
        return None
    condition = attached[1]["condition"]
    modifier = attached[1]["modifier"]
    if any(
        modifier[field]
        for field in (
            "type_operations",
            "remove_abilities",
            "add_rules_text",
            "add_ability_fragments",
        )
    ):
        return None
    return (
        {
            "kind": "attached",
            "target_controller": None,
            "predicate": None,
            "exclude_source": False,
            "types_all": list(condition["types_all"]),
        },
        {
            "add_abilities": list(modifier["add_abilities"]),
            "power": int(modifier["power"]),
            "toughness": int(modifier["toughness"]),
        },
        tuple(attached[2]),
    )


def fixed_public_state_characteristics_handler(
    oracle_line: str,
    *,
    source_name: str,
) -> tuple[str, Mapping[str, Any], tuple[str, ...]] | None:
    """Lower fixed characteristics gated by closed public non-type state.

    Type-dependent counts, dynamic amounts, quoted abilities, characteristic
    changes, combat state, and source attachment-state predicates remain
    residual.  Both represented layers use one target and source condition.
    """

    parsed = _fixed_public_state_parts(
        oracle_line,
        source_name=source_name,
    )
    if parsed is None:
        return None
    source_condition, body = parsed
    compiled = _conditional_target(body, source_name=source_name)
    if compiled is None:
        return None
    target, modifier, body_capabilities = compiled
    return (
        "continuous-fixed-public-state-characteristics-v1",
        {
            "handler_id": FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID,
            "schema_version": 1,
            "event": "characteristics.evaluate",
            "source_condition": source_condition.to_dict(),
            "target": dict(target),
            "modifier": dict(modifier),
        },
        tuple(
            sorted(
                {
                    "continuous.characteristics.fixed_public_state",
                    *body_capabilities,
                }
            )
        ),
    )


def controlled_creature_until_end_of_turn_effect(
    oracle_line: str,
) -> tuple[str, tuple[Mapping[str, Any], ...], tuple[str, ...]] | None:
    parsed = controlled_creature_fixed_modifier(
        oracle_line, until_end_of_turn=True
    )
    if parsed is None:
        return None
    predicate, power, toughness, exclude_source = parsed
    predicate_fields = {
        **predicate.to_dict(),
        "controller": "$controller",
    }
    if exclude_source:
        predicate_fields["exclude_ref"] = "$source"
    return (
        "modify-controlled-creatures-fixed-stats-eot-v1",
        (
            {
                "op": "modify_all_matching_permanents_until_end_of_turn",
                "predicate": ObjectQuerySpec.from_dict(
                    predicate_fields
                ).to_dict(),
                "power": power,
                "toughness": toughness,
            },
        ),
        ("cr-611-continuous-effects",),
    )


def fixed_controlled_characteristic_query_is_closed(
    query: ObjectQuerySpec,
) -> bool:
    """Return whether one resolution-time controlled set is represented."""

    if (
        query.zones != ("battlefield",)
        or query.owner is not None
        or query.controller != "$controller"
        or query.types_any
        or query.excluded_types
        or query.subtypes_any
        or query.excluded_subtypes
        or query.colors_any
        or query.keywords_all
        or query.tapped is not None
        or query.include_phased_out
        or query.known_to_actor is not None
        or query.state_predicate is not None
        or query.exclude_ref not in {None, "$source"}
    ):
        return False
    qualifiers = sum(
        bool(value)
        for value in (
            query.subtypes_all,
            query.supertypes_all,
            query.colors_all,
            query.colorless is not None,
            query.token is not None,
        )
    )
    if query.subtypes_all:
        return (
            query.types_all == ("creature",)
            and qualifiers == 1
            and len(query.subtypes_all) == 1
            and canonical_creature_subtype(query.subtypes_all[0])
            == query.subtypes_all[0]
        )
    if query.supertypes_all:
        return (
            query.types_all == ("creature",)
            and qualifiers == 1
            and query.supertypes_all == ("legendary",)
        )
    if query.colors_all:
        return (
            query.types_all == ("creature",)
            and qualifiers == 1
            and len(query.colors_all) == 1
            and query.colors_all[0] in "WUBRG"
        )
    if query.colorless is not None:
        return (
            query.types_all == ("creature",)
            and qualifiers == 1
            and query.colorless is True
        )
    if query.token is not None:
        return query.types_all == ("creature",) and qualifiers == 1
    return query.types_all in {
        (),
        ("artifact",),
        ("land",),
        ("creature",),
        ("artifact", "creature"),
        ("land", "creature"),
    }


def controlled_characteristic_until_end_of_turn_effect(
    oracle_line: str,
) -> tuple[str, tuple[Mapping[str, Any], ...], tuple[str, ...]] | None:
    """Lower one fixed, resolution-locked controlled characteristic set."""

    existing = controlled_creature_until_end_of_turn_effect(oracle_line)
    if existing is not None:
        return existing
    text = _TRAILING_REMINDER.sub("", oracle_line.strip()).strip()
    prefix = re.fullmatch(
        r"Until end of turn, (?P<body>.+?)\.?",
        text,
        flags=re.IGNORECASE,
    )
    suffix = re.fullmatch(
        r"(?P<body>.+?) until end of turn\.?",
        text,
        flags=re.IGNORECASE,
    )
    match = prefix or suffix
    if match is None:
        return None
    body = match.group("body").rstrip(".")
    existing = controlled_creature_until_end_of_turn_effect(
        f"{body} until end of turn."
    )
    if existing is not None:
        return existing
    normalized = re.sub(
        r"\bgain\b",
        "have",
        body,
        count=1,
        flags=re.IGNORECASE,
    )
    lowered = fixed_query_characteristic_grant_handler(normalized)
    if lowered is None:
        lowered = fixed_query_keyword_grant_handler(normalized)
    if lowered is None:
        return None
    _template_id, descriptor, _capabilities = lowered
    condition = descriptor["condition"]
    if condition["target_controller"] != "source_controller":
        return None
    predicate = ObjectQuerySpec.from_dict(
        {
            **condition["predicate"],
            "controller": "$controller",
            **(
                {"exclude_ref": "$source"}
                if condition["exclude_source"]
                else {}
            ),
        }
    )
    if not fixed_controlled_characteristic_query_is_closed(predicate):
        return None
    modifier = descriptor["modifier"]
    keywords = tuple(modifier["add_abilities"])
    mechanics = tuple(
        sorted(
            {
                "cr-611-continuous-effects",
                *(keyword.casefold() for keyword in keywords),
            }
        )
    )
    return (
        "modify-controlled-fixed-characteristics-eot-v2",
        (
            {
                "op": "modify_all_matching_permanents_until_end_of_turn",
                "predicate": predicate.to_dict(),
                "power": int(modifier.get("power", 0)),
                "toughness": int(modifier.get("toughness", 0)),
                "keywords": list(keywords),
            },
        ),
        mechanics,
    )


def basic_land_type_addition_handler(
    oracle_line: str,
) -> tuple[str, Mapping[str, Any], str] | None:
    """Lower the exact CR 305.7 additive basic-land-type wording.

    This intentionally recognizes only the closed, nonconditional wording.
    Type-setting effects and restricted object sets require different layer-4
    contracts and remain residual rather than being approximated here.
    """

    match = _BASIC_LAND_TYPE_ADDITION.fullmatch(oracle_line.strip())
    if match is None:
        return None
    subtype = match.group("subtype").casefold()
    return (
        "continuous-add-basic-land-type-all-lands-v1",
        {
            "handler_id": "continuous.basic_land_type.add_all_lands.v1",
            "schema_version": 1,
            "event": "characteristics.evaluate",
            "condition": {"target_types_all": ["land"]},
            "modifier": {"basic_land_type": subtype},
        },
        "continuous.basic_land_type.add_all_lands",
    )
