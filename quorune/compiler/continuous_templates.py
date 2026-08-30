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
from .public_state_queries import (
    controlled_creature_fixed_modifier,
    fixed_battlefield_query_subject,
    fixed_power_toughness_battlefield_query,
    fixed_public_state_parts,
)
from ..rules.source_references import SourceReferenceSpec
from ..trigger_participation import WardSpec
from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID,
    FixedPublicStateConditionKind,
)


_BASIC_LAND_TYPE_ADDITION = re.compile(
    r"^Each land is (?:a|an) "
    r"(?P<subtype>Plains|Island|Swamp|Mountain|Forest) "
    r"in addition to its other land types\.?$",
    re.IGNORECASE,
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
    r"^(?P<subject>.+?) get "
    r"(?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) and have "
    r"(?P<abilities>.+?)\.?$",
    re.IGNORECASE,
)
_FIXED_QUERY_POWER_TOUGHNESS = re.compile(
    r"^(?P<subject>.+?) get (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+)\.?$",
    re.IGNORECASE,
)
def fixed_power_toughness_anthem_handler(
    oracle_line: str,
) -> tuple[str, Mapping[str, Any], str] | None:
    text = _TRAILING_REMINDER.sub("", oracle_line.strip()).strip()
    match = _FIXED_QUERY_POWER_TOUGHNESS.fullmatch(text)
    if match is None:
        return None
    subject = match.group("subject")
    parsed = fixed_power_toughness_battlefield_query(subject, text)
    if parsed is None:
        return None
    relation, predicate, exclude_source = parsed
    power = int(match.group("power"))
    toughness = int(match.group("toughness"))
    if power == 0 and toughness == 0:
        return None
    return (
        "continuous-fixed-query-anthem-v2",
        {
            "handler_id": "continuous.anthem.fixed-query.v2",
            "schema_version": 2,
            "event": "characteristics.evaluate",
            "condition": {
                "target_controller": relation,
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

    The grammar represents fixed battlefield sets through one canonical
    ``ObjectQuerySpec`` shared with layer-7c modifiers.  Dynamic counts,
    combat-state, ability-presence, chosen, hidden-zone, and conditional sets
    remain residual.  Every accepted keyword declares its exact trusted combat,
    damage, destruction, or targeting consumer capability.
    """

    text = _TRAILING_REMINDER.sub("", oracle_line.strip()).strip()
    match = re.fullmatch(
        r"(?P<subject>.+?) ha(?:ve|s) (?P<abilities>.+?)\.?",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    parsed = fixed_battlefield_query_subject(match.group("subject"))
    if parsed is None:
        return None
    relation, predicate, exclude_source = parsed
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
    """Lower fixed characteristics gated by closed public state or queries.

    Dynamic quantities, open comparisons, quoted abilities, characteristic
    changes, and ability-presence predicates remain residual. Both represented
    layers use one target and source condition.
    """

    parsed = fixed_public_state_parts(
        oracle_line,
        source_name=source_name,
    )
    if parsed is None:
        return None
    source_condition, body = parsed
    if re.match(r"^it\b", body, re.IGNORECASE):
        if (
            source_condition.kind
            is FixedPublicStateConditionKind.ATTACHED_MATCHES_QUERY
        ):
            assert source_condition.predicate is not None
            attached_subject = (
                "Enchanted land"
                if "land" in source_condition.predicate.types_all
                else "Enchanted creature"
            )
            body = re.sub(
                r"^it\b",
                attached_subject,
                body,
                count=1,
                flags=re.IGNORECASE,
            )
        else:
            body = re.sub(
                r"^it\b",
                "This creature",
                body,
                count=1,
                flags=re.IGNORECASE,
            )
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
