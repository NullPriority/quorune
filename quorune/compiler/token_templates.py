from __future__ import annotations

from itertools import combinations
import json
import re
from typing import Any, Mapping

from ..ability_fragments import (
    AllCreatureTypesCharacteristicDefinitionSpec,
    ability_fragment_to_dict,
)
from ..fixed_token_production import (
    FixedTokenCreationTemplate,
)
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from .fixed_token_production_templates import (
    extended_fixed_token_creation_effect_template,
)


_ADDITIONAL_DEFINITION = (
    r"(?P<definition>Treasure token|Food token|Map token|"
    r"1/1 colorless Thopter artifact creature token with flying)"
)
_CREATOR_FRAME = re.compile(
    r"^If you would create one or more "
    r"(?:(?P<quality>Treasure|artifact) )?tokens, instead create those "
    rf"tokens plus an additional {_ADDITIONAL_DEFINITION}\.?$",
    re.IGNORECASE,
)
_CONTROLLED_FRAME = re.compile(
    r"^If one or more (?:(?P<quality>artifact) )?tokens would be created "
    r"under your control, those tokens plus an additional "
    rf"{_ADDITIONAL_DEFINITION} are created instead\.?$",
    re.IGNORECASE,
)

_TOKEN_TREASURE = "Treasure"
_TOKEN_FOOD = "Food"
_TOKEN_MAP = "Map"
_TOKEN_CLUE = "Clue"
_TOKEN_THOPTER = "Thopter"

FIXED_TOKEN_DEFINITION_BATCH_MECHANIC = "fixed-token-definition-batch"

_COLOR_SYMBOLS = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
}
_COLOR_ORDER = "WUBRG"
_CREATURE_TOKEN_KEYWORDS = frozenset(
    {
        "deathtouch",
        "defender",
        "double strike",
        "first strike",
        "flying",
        "haste",
        "hexproof",
        "indestructible",
        "infect",
        "islandwalk",
        "lifelink",
        "mountainwalk",
        "menace",
        "plainswalk",
        "reach",
        "swampwalk",
        "trample",
        "vigilance",
        "forestwalk",
        "changeling",
    }
)
_FIXED_CREATURE_TOKEN = re.compile(
    rf"^Create (?P<count>{FIXED_COUNT_PATTERN}|eleven|twelve|thirteen) "
    r"(?P<tapped>tapped )?"
    r"(?P<power>\d+)/(?P<toughness>\d+) "
    r"(?P<colors>white|blue|black|red|green|colorless)"
    r"(?: and (?P<second_color>white|blue|black|red|green))? "
    r"(?P<subtypes>[A-Za-z][A-Za-z' -]*?) "
    r"(?P<card_types>(?:(?:artifact|enchantment) )*)creature tokens?"
    r"(?: with (?P<keywords>[A-Za-z ,'-]+))?\.?$",
    re.IGNORECASE,
)
_FIXED_PREDEFINED_TOKEN = re.compile(
    rf"^Create (?P<count>{FIXED_COUNT_PATTERN}|eleven|twelve|thirteen) "
    r"(?P<tapped>tapped )?"
    r"(?P<name>Treasure|Food|Map|Clue|Powerstone|Junk|Vibranium) tokens?\.?$",
    re.IGNORECASE,
)
_TOKEN_DEFINITIONS: dict[str, Mapping[str, Any]] = {
    "treasure token": {
        "name": _TOKEN_TREASURE,
        "type_line": "Token Artifact — Treasure",
        "display_text": (
            "{T}, Sacrifice this token: Add one mana of any color."
        ),
        "ability_profile": "tap_sac_any_color_mana_v1",
    },
    "food token": {
        "name": _TOKEN_FOOD,
        "type_line": "Token Artifact — Food",
        "display_text": (
            "{2}, {T}, Sacrifice this token: You gain 3 life."
        ),
        "ability_profile": "two_tap_sac_gain_three_life_v1",
    },
    "map token": {
        "name": _TOKEN_MAP,
        "type_line": "Token Artifact — Map",
        "display_text": (
            "{1}, {T}, Sacrifice this token: Target creature you control "
            "explores. Activate only as a sorcery."
        ),
        "ability_profile": "one_tap_sac_explore_controlled_creature_v1",
    },
    "clue token": {
        "name": _TOKEN_CLUE,
        "type_line": "Token Artifact — Clue",
        "display_text": (
            "{2}, Sacrifice this token: Draw a card."
        ),
        "ability_profile": "two_sac_draw_card_v1",
    },
    "powerstone token": {
        "name": "Powerstone",
        "type_line": "Token Artifact — Powerstone",
        "display_text": (
            "{T}: Add {C}. This mana can't be spent to cast a nonartifact "
            "spell."
        ),
        "ability_profile": "tap_colorless_restricted_v1",
    },
    "junk token": {
        "name": "Junk",
        "type_line": "Token Artifact — Junk",
        "display_text": (
            "{T}, Sacrifice this token: Exile the top card of your library. "
            "You may play that card this turn. Activate only as a sorcery."
        ),
        "ability_profile": "tap_sac_impulse_one_v1",
    },
    "vibranium token": {
        "name": "Vibranium",
        "type_line": "Token Artifact — Vibranium",
        "display_text": (
            "{T}: Add {C}. This mana can't be spent to cast a nonartifact "
            "spell."
        ),
        "keywords": ["Indestructible"],
        "ability_profile": "tap_colorless_restricted_v1",
    },
    "1/1 colorless thopter artifact creature token with flying": {
        "name": _TOKEN_THOPTER,
        "type_line": "Token Artifact Creature — Thopter",
        "colors": [],
        "power": "1",
        "toughness": "1",
        "keywords": ["Flying"],
    },
}

_PREDEFINED_CREATION_DEFINITIONS: dict[str, Mapping[str, Any]] = {
    key: {
        **{
            field: value
            for field, value in definition.items()
            if field not in {"ability_profile"}
        },
        **(
            {
                "activated_ability_profile": definition["ability_profile"],
            }
            if "ability_profile" in definition
            else {}
        ),
    }
    for key, definition in _TOKEN_DEFINITIONS.items()
    if key
    in {
        "treasure token",
        "food token",
        "map token",
        "clue token",
        "powerstone token",
        "junk token",
        "vibranium token",
    }
}


def _fixed_keyword_list(value: str | None) -> tuple[str, ...] | None:
    if value is None:
        return ()
    normalized = re.sub(r",?\s+and\s+", ",", value.casefold())
    keywords = tuple(
        part.strip() for part in normalized.split(",") if part.strip()
    )
    if (
        not keywords
        or len(keywords) != len(set(keywords))
        or any(keyword not in _CREATURE_TOKEN_KEYWORDS for keyword in keywords)
    ):
        return None
    return keywords


def _fixed_colors(first: str, second: str | None) -> list[str] | None:
    normalized = first.casefold()
    if normalized == "colorless":
        return [] if second is None else None
    names = (normalized, *(() if second is None else (second.casefold(),)))
    symbols = tuple(_COLOR_SYMBOLS[name] for name in names)
    if len(symbols) != len(set(symbols)):
        return None
    return sorted(symbols, key=_COLOR_ORDER.index)


def _positive_fixed_number(value: str) -> int | None:
    amount = {
        "eleven": 11,
        "twelve": 12,
        "thirteen": 13,
    }.get(value.casefold())
    if amount is None:
        amount = fixed_number(value)
    return amount if amount > 0 else None


def _fixed_creature_token_types(value: str) -> tuple[str, ...] | None:
    types = tuple(part.casefold() for part in value.split())
    if len(types) != len(set(types)) or not set(types).issubset(
        {"artifact", "enchantment"}
    ):
        return None
    return tuple(
        card_type
        for card_type in ("artifact", "enchantment")
        if card_type in types
    )


def _single_fixed_token_creation_effect_template(
    text: str,
) -> FixedTokenCreationTemplate | None:
    """Lower one complete fixed token-definition instruction.

    Dynamic quantities, source-relative or modified copies, open ability text,
    attached or attacking tokens, and compound instructions remain residual.
    """

    normalized = " ".join(text.split())
    extended = extended_fixed_token_creation_effect_template(
        normalized,
        compile_inner=_single_fixed_token_creation_effect_template,
    )
    if extended is not None:
        return extended
    predefined = _FIXED_PREDEFINED_TOKEN.fullmatch(normalized)
    if predefined is not None:
        quantity = _positive_fixed_number(predefined.group("count"))
        if quantity is None:
            return None
        name = predefined.group("name").casefold()
        definition = _PREDEFINED_CREATION_DEFINITIONS.get(f"{name} token")
        if definition is None:
            return None
        return FixedTokenCreationTemplate(
            template_id=f"create-fixed-{name}-token-v1",
            effect={
                "op": "create_token",
                "controller": "$controller",
                "name": str(definition["name"]),
                "quantity": quantity,
                **(
                    {"tapped": True}
                    if predefined.group("tapped")
                    else {}
                ),
                "characteristics": {
                    field: value
                    for field, value in definition.items()
                    if field != "name"
                },
            },
            mechanics=(
                "cr-111-tokens",
                *(
                    str(keyword).casefold()
                    for keyword in definition.get("keywords", [])
                ),
            ),
        )

    creature = _FIXED_CREATURE_TOKEN.fullmatch(normalized)
    if creature is None:
        return None
    quantity = _positive_fixed_number(creature.group("count"))
    if quantity is None:
        return None
    colors = _fixed_colors(
        creature.group("colors"), creature.group("second_color")
    )
    keywords = _fixed_keyword_list(creature.group("keywords"))
    card_types = _fixed_creature_token_types(creature.group("card_types"))
    if colors is None or keywords is None or card_types is None:
        return None
    subtypes = " ".join(creature.group("subtypes").split())
    ordered_types = (
        *(("Artifact",) if "artifact" in card_types else ()),
        "Creature",
        *(("Enchantment",) if "enchantment" in card_types else ()),
    )
    characteristics: dict[str, Any] = {
        "type_line": f"Token {' '.join(ordered_types)} — {subtypes}",
        "colors": colors,
        "power": creature.group("power"),
        "toughness": creature.group("toughness"),
    }
    if keywords:
        characteristics["keywords"] = [
            keyword.title() for keyword in keywords
        ]
    if "changeling" in keywords:
        characteristics["ability_fragments"] = [
            ability_fragment_to_dict(
                AllCreatureTypesCharacteristicDefinitionSpec()
            )
        ]
    return FixedTokenCreationTemplate(
        template_id="create-fixed-creature-token-v2",
        effect={
            "op": "create_token",
            "controller": "$controller",
            "name": subtypes,
            "quantity": quantity,
            **({"tapped": True} if creature.group("tapped") else {}),
            "characteristics": characteristics,
        },
        mechanics=("cr-111-tokens", *keywords),
    )


def _batch_boundaries(text: str) -> tuple[tuple[int, int], ...]:
    return tuple(
        (match.start(), match.end())
        for match in re.finditer(
            r",\s+and\s+|,\s+|\s+and\s+",
            text,
            re.IGNORECASE,
        )
    )


def _batch_parts(
    text: str,
    boundaries: tuple[tuple[int, int], ...],
) -> tuple[str, ...]:
    parts: list[str] = []
    start = 0
    for left, right in boundaries:
        parts.append(text[start:left])
        start = right
    parts.append(text[start:])
    return tuple(part.strip() for part in parts)


def _batch_effect_key(
    templates: tuple[FixedTokenCreationTemplate, ...],
) -> str:
    return json.dumps(
        [template.effect for template in templates],
        sort_keys=True,
        separators=(",", ":"),
    )


def _fixed_token_batch_creation_effect_template(
    text: str,
) -> FixedTokenCreationTemplate | None:
    normalized = " ".join(text.split()).rstrip(".")
    if not normalized.startswith("Create "):
        return None
    boundaries = _batch_boundaries(normalized)
    candidates: dict[
        str, tuple[FixedTokenCreationTemplate, ...]
    ] = {}
    for split_count in (1, 2):
        for selected in combinations(boundaries, split_count):
            if any(
                selected[index][1] > selected[index + 1][0]
                for index in range(len(selected) - 1)
            ):
                continue
            parts = _batch_parts(normalized, selected)
            templates: list[FixedTokenCreationTemplate] = []
            for index, part in enumerate(parts):
                candidate = (part if index == 0 else f"Create {part}") + "."
                template = _single_fixed_token_creation_effect_template(
                    candidate
                )
                if template is None or template.target_schema is not None:
                    break
                templates.append(template)
            else:
                compiled = tuple(templates)
                candidates[_batch_effect_key(compiled)] = compiled
    if len(candidates) != 1:
        return None
    templates = next(iter(candidates.values()))
    return FixedTokenCreationTemplate(
        template_id="create-fixed-token-definition-batch-v1",
        effect={
            "op": "create_token_batch",
            "controller": "$controller",
            "tokens": [
                {
                    field: value
                    for field, value in template.effect.items()
                    if field not in {"op", "controller"}
                }
                for template in templates
            ],
        },
        mechanics=tuple(
            dict.fromkeys(
                (
                    FIXED_TOKEN_DEFINITION_BATCH_MECHANIC,
                    *(
                        mechanic
                        for template in templates
                        for mechanic in template.mechanics
                    ),
                )
            )
        ),
    )


def fixed_token_creation_effect_template(
    text: str,
) -> FixedTokenCreationTemplate | None:
    """Lower one closed fixed token-definition instruction.

    A single fixed definition or an unambiguous two- or three-definition
    simultaneous nontargeted batch is accepted. Dynamic quantities,
    source-relative or modified copies, open ability text, attachments,
    attacking tokens, and compound non-token instructions remain residual.
    """

    return _single_fixed_token_creation_effect_template(
        text
    ) or _fixed_token_batch_creation_effect_template(text)


def static_additional_token_replacement_handler(
    text: str,
) -> tuple[str, Mapping[str, Any], str] | None:
    """Lower the closed mandatory fixed additional-token wording family."""

    normalized = text.strip()
    match = _CREATOR_FRAME.fullmatch(normalized)
    if match is None:
        match = _CONTROLLED_FRAME.fullmatch(normalized)
    if match is None:
        return None

    quality = str(match.group("quality") or "").casefold()
    definition_key = " ".join(
        match.group("definition").casefold().split()
    )
    definition = _TOKEN_DEFINITIONS.get(definition_key)
    if definition is None:
        return None
    created_types = ["artifact"] if quality == "artifact" else []
    treasure_subtype = _TOKEN_TREASURE.casefold()
    created_subtypes = [treasure_subtype] if quality == treasure_subtype else []
    filter_label = quality or "any"
    token_label = str(definition["name"]).casefold()
    return (
        f"additional-token-fixed-{filter_label}-{token_label}-v1",
        {
            "handler_id": "replacement.token.additional.v2",
            "schema_version": 2,
            "event": "token.create",
            "condition": {
                "event_controller": "source_controller",
                "created_types_all": created_types,
                "created_subtypes_all": created_subtypes,
            },
            "quantity": 1,
            "token": dict(definition),
        },
        "token.creation.additional_replacement",
    )


__all__ = [
    "FIXED_TOKEN_DEFINITION_BATCH_MECHANIC",
    "FixedTokenCreationTemplate",
    "fixed_token_creation_effect_template",
    "static_additional_token_replacement_handler",
]
