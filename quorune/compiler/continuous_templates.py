from __future__ import annotations

from dataclasses import replace
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
from .creature_subtypes import CREATURE_SUBTYPES, canonical_creature_subtype
from .public_state_queries import (
    controlled_creature_fixed_modifier,
    fixed_battlefield_query_subject,
    fixed_power_toughness_battlefield_query,
    fixed_public_state_parts,
)
from .query_characteristic_templates import query_characteristic_quantity
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
        "fear",
        "flying",
        "haste",
        "hexproof",
        "indestructible",
        "infect",
        "intimidate",
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

_ATTACHED_QUOTED_ABILITY_SENTINELS = (
    "Flying",
    "Reach",
    "Vigilance",
    "Haste",
)


def _fixed_query_quoted_ability_shell(
    oracle_line: str,
) -> tuple[
    str,
    tuple[str, Mapping[str, Any], tuple[str, ...]],
] | None:
    """Parse one quoted ability behind an existing public battlefield query."""

    text = _TRAILING_REMINDER.sub("", oracle_line.strip()).strip()
    if text.count('"') != 2:
        return None
    quote_start = text.find('"')
    quote_end = text.rfind('"')
    quoted = text[quote_start + 1 : quote_end].strip()
    if not quoted or "\n" in quoted:
        return None
    for sentinel in _ATTACHED_QUOTED_ABILITY_SENTINELS:
        synthetic = text[:quote_start] + sentinel + text[quote_end + 1 :]
        compiled = fixed_query_keyword_grant_handler(synthetic)
        if compiled is None:
            continue
        modifier = compiled[1].get("modifier")
        if not isinstance(modifier, Mapping) or list(
            modifier.get("add_abilities", ())
        ) != [sentinel]:
            continue
        return quoted, compiled
    return None


def fixed_query_quoted_ability_text(oracle_line: str) -> str | None:
    """Return the sole quote when a closed live battlefield query owns it."""

    shell = _fixed_query_quoted_ability_shell(oracle_line)
    return shell[0] if shell is not None else None


def fixed_query_quoted_ability_handler(
    oracle_line: str,
    *,
    fragment: Mapping[str, Any],
    fragment_capabilities: tuple[str, ...],
) -> tuple[str, Mapping[str, Any], tuple[str, ...]] | None:
    """Grant one independently exact typed ability to a queried live set."""

    shell = _fixed_query_quoted_ability_shell(oracle_line)
    if shell is None:
        return None
    _quoted, compiled = shell
    condition = compiled[1].get("condition")
    if not isinstance(condition, Mapping):
        return None
    return (
        "continuous-fixed-query-granted-ability-v1",
        {
            "handler_id": "continuous.ability.fixed-query-grant.v1",
            "schema_version": 1,
            "event": "characteristics.evaluate",
            "condition": dict(condition),
            "modifier": {"add_ability_fragments": [dict(fragment)]},
        },
        tuple(
            sorted(
                {
                    "continuous.ability.fixed_query_grant",
                    *fragment_capabilities,
                }
            )
        ),
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


_ATTACHED_COLOR_SYMBOLS = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
}


def _attached_modifier() -> dict[str, Any]:
    return {
        "type_operations": [],
        "color_operations": [],
        "add_abilities": [],
        "remove_abilities": [],
        "remove_all_abilities": False,
        "add_rules_text": [],
        "add_ability_fragments": [],
        "power": 0,
        "toughness": 0,
        "base_power": None,
        "base_toughness": None,
        "quantity": None,
        "quantity_power": 0,
        "quantity_toughness": 0,
    }


def _grant_attached_abilities(
    modifier: dict[str, Any], value: str
) -> bool:
    granted = _attached_granted_abilities(value)
    if granted is None:
        return False
    abilities, fragments = granted
    if set(modifier["add_abilities"]).intersection(abilities):
        return False
    modifier["add_abilities"].extend(abilities)
    modifier["add_ability_fragments"].extend(fragments)
    return True


def _attached_type_values(value: str) -> tuple[str, ...] | None:
    phrase = re.sub(r"^(?:a|an)\s+", "", value.strip(), flags=re.IGNORECASE)
    whole = canonical_creature_subtype(phrase)
    if whole is not None:
        return (whole.title(),)
    values = tuple(part for part in phrase.split() if part)
    canonical = tuple(canonical_creature_subtype(part) for part in values)
    if not values or any(value is None for value in canonical):
        return None
    return tuple(str(value).title() for value in canonical)


def _add_attached_type_phrase(
    modifier: dict[str, Any],
    value: str,
    *,
    addition: bool,
) -> bool:
    phrase = re.sub(r"^(?:a|an)\s+", "", value.strip(), flags=re.IGNORECASE)
    if phrase.casefold() in {"all creature types", "every creature type"}:
        modifier["type_operations"].append(
            {
                "op": "add_types",
                "field": "subtypes",
                "values": sorted(value.title() for value in CREATURE_SUBTYPES),
            }
        )
        return True
    if phrase.casefold() == "legendary":
        modifier["type_operations"].append(
            {
                "op": "add_types",
                "field": "supertypes",
                "values": ["Legendary"],
            }
        )
        return True
    if phrase.casefold() in _CARD_TYPE_WORDS:
        modifier["type_operations"].append(
            {
                "op": "add_types" if addition else "set_types",
                "field": "card_types",
                "values": [phrase.title()],
            }
        )
        return True
    subtypes = _attached_type_values(phrase)
    if subtypes is None:
        return False
    modifier["type_operations"].append(
        {
            "op": "add_types" if addition else "set_types",
            "field": "subtypes",
            "values": list(subtypes),
        }
    )
    return True


def _attached_definition(
    modifier: dict[str, Any],
    value: str,
    *,
    addition: bool,
) -> bool:
    phrase = re.sub(r"^(?:a|an)\s+", "", value.strip(), flags=re.IGNORECASE)
    if re.search(r"\bnamed\b", phrase, re.IGNORECASE):
        return False
    colors: list[str] = []
    if phrase.casefold().startswith("colorless "):
        modifier["color_operations"].append({"op": "remove_all_colors"})
        phrase = phrase[len("colorless ") :]
    else:
        while True:
            match = re.match(
                r"^(white|blue|black|red|green)(?: and )?\s+",
                phrase,
                re.IGNORECASE,
            )
            if match is None:
                break
            colors.append(_ATTACHED_COLOR_SYMBOLS[match.group(1).casefold()])
            phrase = phrase[match.end() :]
        if colors:
            modifier["color_operations"].append(
                {
                    "op": "add_colors" if addition else "set_colors",
                    "values": colors,
                }
            )
    words = phrase.split()
    card_types = [
        word.title()
        for word in words
        if word.casefold() in _CARD_TYPE_WORDS
    ]
    subtype_words = [
        word for word in words if word.casefold() not in _CARD_TYPE_WORDS
    ]
    if card_types:
        modifier["type_operations"].append(
            {
                "op": "add_types" if addition else "set_types",
                "field": "card_types",
                "values": card_types,
            }
        )
    if subtype_words and not _add_attached_type_phrase(
        modifier,
        " ".join(subtype_words),
        addition=addition,
    ):
        return False
    return bool(card_types or subtype_words)


def _attached_dynamic_modifier(
    body: str,
    *,
    source_name: str,
) -> dict[str, Any] | None:
    modifier = _attached_modifier()
    leading = re.fullmatch(
        r"has (?P<abilities>.+?) and (?P<remainder>gets .+)",
        body,
        re.IGNORECASE,
    )
    if leading is not None:
        if not _grant_attached_abilities(modifier, leading.group("abilities")):
            return None
        body = leading.group("remainder")

    fixed = re.fullmatch(
        r"gets (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) for each "
        r"(?P<quantity>.+?)(?:(?: and has (?P<abilities>.+))|"
        r"(?: and is (?P<type>.+?) in addition to its other types))?",
        body,
        re.IGNORECASE,
    )
    variable = re.fullmatch(
        r"gets (?P<power>[+-](?:X|\d+))/(?P<toughness>[+-](?:X|\d+)), "
        r"where X is the number of (?P<quantity>.+)",
        body,
        re.IGNORECASE,
    )
    match = fixed or variable
    if match is None:
        return None
    quantity_text = match.group("quantity")
    if re.search(
        r"(?:attached to (?:it|this creature)|\bits controller's\b|"
        r"\bof its\b|\bon it\b)",
        quantity_text,
        re.IGNORECASE,
    ):
        return None
    quantity = query_characteristic_quantity(
        quantity_text,
        source_name=source_name,
        definition_extensions=True,
    )
    if quantity is None:
        return None
    if (
        quantity.exclude_source
        and re.match(r"other creatures?\b", quantity_text, re.IGNORECASE)
    ):
        quantity = replace(
            quantity,
            exclude_source=False,
            exclude_attached_object=True,
        )

    for field in ("power", "toughness"):
        raw = match.group(field).upper()
        if raw in {"+X", "-X"}:
            modifier[f"quantity_{field}"] = 1 if raw == "+X" else -1
        else:
            value = int(raw)
            if fixed is not None:
                modifier[f"quantity_{field}"] = value
            else:
                modifier[field] = value
    modifier["quantity"] = quantity.to_dict()
    abilities = match.groupdict().get("abilities")
    if abilities and not _grant_attached_abilities(modifier, abilities):
        return None
    added_type = match.groupdict().get("type")
    if added_type and not _add_attached_type_phrase(
        modifier,
        added_type,
        addition=True,
    ):
        return None
    return modifier


def _attached_compound_modifier(body: str) -> dict[str, Any] | None:
    modifier = _attached_modifier()
    pt_type = re.fullmatch(
        r"gets (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+)(?:, has "
        r"(?P<abilities>.+?),)? and is (?P<type>.+?)(?: in addition to "
        r"its other types)?",
        body,
        re.IGNORECASE,
    )
    legendary = re.fullmatch(
        r"is legendary, gets (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+), "
        r"and has (?P<abilities>.+)",
        body,
        re.IGNORECASE,
    )
    ability_removal = re.fullmatch(
        r"(?:gets (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) and )?"
        r"(?:(?:has (?P<abilities>.+?) and loses (?P<removed>.+))|"
        r"(?:loses (?P<all>all abilities)))",
        body,
        re.IGNORECASE,
    )
    match = pt_type or legendary or ability_removal
    if match is None:
        return None
    values = match.groupdict()
    if values.get("power") is not None:
        modifier["power"] = int(values["power"])
        modifier["toughness"] = int(values["toughness"])
    if match is legendary:
        if not _add_attached_type_phrase(
            modifier, "legendary", addition=True
        ):
            return None
    added_type = values.get("type")
    if added_type and not _add_attached_type_phrase(
        modifier,
        added_type,
        addition=True,
    ):
        return None
    abilities = values.get("abilities")
    if abilities and not _grant_attached_abilities(modifier, abilities):
        return None
    removed = values.get("removed")
    if removed:
        parsed = _attached_abilities(removed)
        if parsed is None or _toxic_ability_fragments(parsed):
            return None
        modifier["remove_abilities"].extend(parsed)
    if values.get("all"):
        modifier["remove_all_abilities"] = True
    return modifier


def _attached_transformation_modifier(body: str) -> dict[str, Any] | None:
    modifier = _attached_modifier()
    simple_base = re.fullmatch(
        r"has base power and toughness (?P<power>-?\d+)/(?P<toughness>-?\d+)"
        r"(?:,? (?:and )?has (?P<abilities>.+?))?"
        r"(?P<remove_all>,? and loses all other abilities)?",
        body,
        re.IGNORECASE,
    )
    loses_then_definition = re.fullmatch(
        r"loses all abilities and is (?P<definition>.+?) with base power and "
        r"toughness (?P<power>-?\d+)/(?P<toughness>-?\d+)"
        r"(?P<addition> in addition to its other types)?",
        body,
        re.IGNORECASE,
    )
    definition_then_loses = re.fullmatch(
        r"is (?P<definition>.+?) with base power and toughness "
        r"(?P<power>-?\d+)/(?P<toughness>-?\d+) and loses all abilities",
        body,
        re.IGNORECASE,
    )
    loses_then_base = re.fullmatch(
        r"loses all abilities and has base power and toughness "
        r"(?P<power>-?\d+)/(?P<toughness>-?\d+)",
        body,
        re.IGNORECASE,
    )
    modifier_then_loses = re.fullmatch(
        r"gets (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) and loses all "
        r"abilities",
        body,
        re.IGNORECASE,
    )
    darksteel = re.fullmatch(
        r"is (?P<definition>.+?) with base power and toughness "
        r"(?P<power>-?\d+)/(?P<toughness>-?\d+) and has "
        r"(?P<abilities>.+?), and it loses all other abilities, card types, "
        r"and creature types",
        body,
        re.IGNORECASE,
    )
    spider = re.fullmatch(
        r"is a (?P<definition>.+?) with base power and toughness "
        r"(?P<power>-?\d+)/(?P<toughness>-?\d+)\. It has "
        r"(?P<abilities>.+?) and loses all other abilities",
        body,
        re.IGNORECASE,
    )
    match = (
        darksteel
        or spider
        or loses_then_definition
        or definition_then_loses
        or loses_then_base
        or modifier_then_loses
        or simple_base
    )
    if match is None:
        return None
    values = match.groupdict()
    if match is modifier_then_loses:
        modifier["power"] = int(values["power"])
        modifier["toughness"] = int(values["toughness"])
    else:
        modifier["base_power"] = int(values["power"])
        modifier["base_toughness"] = int(values["toughness"])
    if match in {
        darksteel,
        spider,
        loses_then_definition,
        definition_then_loses,
    }:
        if not _attached_definition(
            modifier,
            values["definition"],
            addition=bool(values.get("addition")),
        ):
            return None
    if match is not simple_base or values.get("remove_all"):
        modifier["remove_all_abilities"] = True
    abilities = values.get("abilities")
    if abilities and not _grant_attached_abilities(modifier, abilities):
        return None
    return modifier


def attached_fixed_characteristics_handler(
    oracle_line: str,
    *,
    source_name: str = "source",
) -> tuple[str, Mapping[str, Any], tuple[str, ...]] | None:
    """Lower one closed attached-object characteristic sentence.

    Conditions, target-relative attachment counts, names, declaration rules,
    quoted rules text, and unrepresented mechanics remain residual.
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
    modifier = (
        _attached_dynamic_modifier(body, source_name=source_name)
        or _attached_transformation_modifier(body)
        or _attached_compound_modifier(body)
    )
    if modifier is None:
        modifier = _attached_modifier()

    pt_match = _ATTACHED_FIXED_PT.fullmatch(body)
    ability_match = _ATTACHED_HAS_OR_LOSES.fullmatch(body)
    type_match = _ATTACHED_ADDED_TYPE.fullmatch(body)
    if any(
        (
            modifier["type_operations"],
            modifier["color_operations"],
            modifier["add_abilities"],
            modifier["remove_abilities"],
            modifier["remove_all_abilities"],
            modifier["power"],
            modifier["toughness"],
            modifier["base_power"] is not None,
            modifier["quantity"] is not None,
        )
    ):
        pass
    elif pt_match is not None:
        modifier["power"] = int(pt_match.group("power"))
        modifier["toughness"] = int(pt_match.group("toughness"))
        if pt_match.group("abilities"):
            if not _grant_attached_abilities(
                modifier, pt_match.group("abilities")
            ):
                return None
    elif ability_match is not None:
        parsed = _attached_abilities(ability_match.group("abilities"))
        if ability_match.group("verb").casefold() == "has":
            if not _grant_attached_abilities(
                modifier, ability_match.group("abilities")
            ):
                return None
        else:
            if parsed is None:
                return None
            if _toxic_ability_fragments(parsed):
                # Removing a typed granted/printed ability requires a closed
                # fragment-removal descriptor, which this handler does not yet
                # own. Do not leave the executable fragment behind.
                return None
            modifier["remove_abilities"].extend(parsed)
    elif type_match is not None:
        if not _add_attached_type_phrase(
            modifier,
            type_match.group("type"),
            addition=True,
        ):
            return None
    else:
        return None

    if not (
        modifier["type_operations"]
        or modifier["color_operations"]
        or modifier["add_abilities"]
        or modifier["remove_abilities"]
        or modifier["remove_all_abilities"]
        or modifier["add_ability_fragments"]
        or modifier["power"]
        or modifier["toughness"]
        or modifier["base_power"] is not None
        or modifier["quantity"] is not None
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
                **modifier,
            },
        },
        tuple(
            sorted(
                {
                    "continuous.attached.fixed_characteristics",
                    *_attached_ability_capabilities(
                        (
                            *modifier["add_abilities"],
                            *modifier["remove_abilities"],
                        )
                    ),
                }
            )
        ),
    )


def _attached_quoted_ability_shell(
    oracle_line: str,
    *,
    source_name: str,
) -> tuple[
    str,
    tuple[str, Mapping[str, Any], tuple[str, ...]],
    str,
] | None:
    """Parse one quoted ability without widening the outer attachment grammar."""

    text = _TRAILING_REMINDER.sub("", oracle_line.strip()).strip()
    if text.count('"') != 2:
        return None
    quote_start = text.find('"')
    quote_end = text.rfind('"')
    quoted = text[quote_start + 1 : quote_end].strip()
    if (
        not quoted
        or "\n" in quoted
        or not text[:quote_start].casefold().startswith(
            ("enchanted creature ", "equipped creature ")
        )
    ):
        return None
    lowered = text.casefold()
    for sentinel in _ATTACHED_QUOTED_ABILITY_SENTINELS:
        if sentinel.casefold() in lowered:
            continue
        synthetic = (
            text[:quote_start]
            + sentinel
            + text[quote_end + 1 :]
        )
        compiled = attached_fixed_characteristics_handler(
            synthetic,
            source_name=source_name,
        )
        if compiled is None:
            continue
        modifier = compiled[1].get("modifier")
        if not isinstance(modifier, Mapping) or list(
            modifier.get("add_abilities", ())
        ).count(sentinel) != 1:
            continue
        return quoted, compiled, sentinel
    return None


def attached_quoted_ability_text(
    oracle_line: str,
    *,
    source_name: str = "source",
) -> str | None:
    """Return the sole quoted ability when the existing outer owner accepts it."""

    shell = _attached_quoted_ability_shell(
        oracle_line,
        source_name=source_name,
    )
    return shell[0] if shell is not None else None


def attached_quoted_ability_handler(
    oracle_line: str,
    *,
    fragment: Mapping[str, Any],
    fragment_capabilities: tuple[str, ...],
    source_name: str = "source",
) -> tuple[str, Mapping[str, Any], tuple[str, ...]] | None:
    """Add one separately compiled typed ability to an accepted outer shell."""

    shell = _attached_quoted_ability_shell(
        oracle_line,
        source_name=source_name,
    )
    if shell is None:
        return None
    _quoted, compiled, sentinel = shell
    _template_id, raw_handler, raw_capabilities = compiled
    handler = dict(raw_handler)
    modifier = {
        key: list(value) if isinstance(value, list) else value
        for key, value in dict(handler["modifier"]).items()
    }
    modifier["add_abilities"].remove(sentinel)
    modifier["add_ability_fragments"].append(dict(fragment))
    handler["modifier"] = modifier
    sentinel_capabilities = _attached_ability_capabilities((sentinel,))
    return (
        "continuous-attached-fixed-characteristics-granted-ability-v1",
        handler,
        tuple(
            sorted(
                {
                    *(
                        capability
                        for capability in raw_capabilities
                        if capability not in sentinel_capabilities
                    ),
                    *fragment_capabilities,
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
            "color_operations",
            "remove_abilities",
            "remove_all_abilities",
            "add_rules_text",
            "add_ability_fragments",
            "base_power",
            "base_toughness",
            "quantity",
            "quantity_power",
            "quantity_toughness",
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
