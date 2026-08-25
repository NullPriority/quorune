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
from .creature_subtypes import canonical_creature_subtype
from ..rules.source_references import SourceReferenceSpec
from ..trigger_participation import WardSpec


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
_FIXED_QUERY_COMBAT_KEYWORDS = frozenset(
    {
        "Deathtouch",
        "Defender",
        "Double Strike",
        "First Strike",
        "Flying",
        "Haste",
        "Hexproof",
        "Indestructible",
        "Infect",
        "Lifelink",
        "Menace",
        "Reach",
        "Shadow",
        "Shroud",
        "Trample",
        "Vigilance",
        "Wither",
    }
)
_FIXED_QUERY_KEYWORD_CAPABILITIES = {
    "Deathtouch": (
        "combat.damage.assignment.deathtouch",
        "damage.result.deathtouch",
    ),
    "Defender": ("combat.attack.defender",),
    "Double Strike": ("combat.damage.participation.strike_steps",),
    "First Strike": ("combat.damage.participation.strike_steps",),
    "Flying": ("combat.block.flying",),
    "Haste": (
        "activation.tap_untap_cost.haste",
        "combat.attack.haste",
    ),
    "Hexproof": ("target.protection.hexproof_permanent",),
    "Indestructible": ("permanent.indestructible.ordinary",),
    "Infect": ("damage.result.infect",),
    "Lifelink": ("damage.result.lifelink",),
    "Menace": ("combat.block.menace",),
    "Reach": ("combat.block.reach",),
    "Shadow": ("combat.block.shadow",),
    "Shroud": ("target.protection.shroud_permanent",),
    "Trample": ("combat.damage.assignment.trample",),
    "Vigilance": ("combat.attack.vigilance",),
    "Wither": ("damage.result.wither",),
}
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
        ability not in _FIXED_QUERY_COMBAT_KEYWORDS for ability in abilities
    ):
        return None
    capabilities = {
        "continuous.ability.fixed_query_keyword_grant",
        *(
            capability
            for ability in abilities
            for capability in _FIXED_QUERY_KEYWORD_CAPABILITIES[ability]
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
    return rf"(?:This creature|This token|{source})"


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
                _FIXED_QUERY_KEYWORD_CAPABILITIES.get(ability, ())
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
