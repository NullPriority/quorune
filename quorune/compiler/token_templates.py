from __future__ import annotations

from itertools import combinations
import json
from . import regex_working_set as re
from typing import Any, Mapping

from ..ability_fragments import (
    AllCreatureTypesCharacteristicDefinitionSpec,
    SpellCastKeywordTriggerKind,
    SpellCastKeywordTriggerSpec,
    ToxicSpec,
    ability_fragment_to_dict,
    decayed_ability_fragments,
)
from ..fixed_token_production import (
    CREATED_TOKEN_HASTE_FIELD,
    FOOD_TOKEN_CHARACTERISTICS,
    GOLD_TOKEN_CHARACTERISTICS,
    TREASURE_TOKEN_CHARACTERISTICS,
    TOKEN_LITERAL_NAME_VERSION_FIELD,
    FixedTokenCreationTemplate,
    fixed_ordinary_token_name,
    predefined_token_identity,
)
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from ..keyword_abilities import triggered_keyword_grant
from ..standard_token_abilities import TOKEN_ABILITY_PROFILE_FIELD
from .fixed_token_production_templates import (
    extended_fixed_token_creation_effect_template,
)


_ADDITIONAL_DEFINITION = r"(?P<definition>.+?)"
_CREATOR_FRAME = re.compile(
    r"^If you would create one or more "
    r"(?:(?P<quality>Treasure|artifact|creature) )?tokens, instead create those "
    rf"tokens plus (?:an additional|a) {_ADDITIONAL_DEFINITION}\.?$",
    re.IGNORECASE,
)
_CONTROLLED_FRAME = re.compile(
    r"^If one or more (?:(?P<quality>artifact|creature) )?tokens would be created "
    r"under your control, those tokens plus (?:an additional|a) "
    rf"{_ADDITIONAL_DEFINITION} are created instead\.?$",
    re.IGNORECASE,
)
_TOKEN_TREASURE = "Treasure"
_TOKEN_FOOD = "Food"
_TOKEN_MAP = "Map"
_TOKEN_CLUE = "Clue"

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
        "banding",
        "decayed",
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
        "prowess",
    }
)
_FIXED_CREATURE_TOKEN = re.compile(
    rf"^Create (?P<count>{FIXED_COUNT_PATTERN}|an|eleven|twelve|thirteen) "
    r"(?P<tapped>tapped )?"
    r"(?P<power>\d+)/(?P<toughness>\d+) "
    r"(?P<colors>white|blue|black|red|green|colorless)"
    r"(?: and (?P<second_color>white|blue|black|red|green))? "
    r"(?P<subtypes>[A-Za-z][A-Za-z' -]*?) "
    r"(?P<card_types>(?:(?:artifact|enchantment|land) )*)creature tokens?"
    r"(?: with (?P<keywords>[A-Za-z0-9 ,'-]+))?\.?$",
    re.IGNORECASE,
)
_FIXED_PREDEFINED_TOKEN = re.compile(
    rf"^Create (?P<count>{FIXED_COUNT_PATTERN}|eleven|twelve|thirteen|fourteen) "
    r"(?P<tapped>tapped )?"
    r"(?P<name>Treasure|Gold|Food|Map|Clue|Powerstone|Junk|Vibranium|Blood|Lander|Mutagen|Walker) tokens?\.?$",
    re.IGNORECASE,
)
_TOKEN_DEFINITIONS: dict[str, Mapping[str, Any]] = {
    "walker token": {
        "name": "Walker", "type_line": "Token Creature — Zombie",
        "colors": ["B"], "power": "2", "toughness": "2", "literal_token_name_version": 1,
    },
    "lander token": {
        "name": "Lander", "type_line": "Token Artifact — Lander",
        "display_text": "{2}, {T}, Sacrifice this token: Search your library for a basic land card, put it onto the battlefield tapped, then shuffle.",
        "ability_profile": "two_tap_sac_basic_land_search_v1",
    },
    "mutagen token": {
        "name": "Mutagen", "type_line": "Token Artifact — Mutagen",
        "display_text": "{1}, {T}, Sacrifice this token: Put a +1/+1 counter on target creature. Activate only as a sorcery.",
        "ability_profile": "one_tap_sac_target_counter_v1",
    },
    "blood token": {
        "name": "Blood",
        "type_line": "Token Artifact — Blood",
        "display_text": "{1}, {T}, Discard a card, Sacrifice this token: Draw a card.",
        "ability_profile": "one_tap_discard_sac_draw_card_v1",
    },
    "treasure token": {"name": _TOKEN_TREASURE, **TREASURE_TOKEN_CHARACTERISTICS},
    "gold token": {"name": "Gold", **GOLD_TOKEN_CHARACTERISTICS},
    "food token": {
        "name": _TOKEN_FOOD,
        **FOOD_TOKEN_CHARACTERISTICS,
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
        "lander token", "mutagen token", "walker token",
        "blood token",
        "treasure token",
        "gold token",
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
        or any(
            keyword not in _CREATURE_TOKEN_KEYWORDS
            and re.fullmatch(r"toxic [1-9]\d*", keyword) is None
            and triggered_keyword_grant(keyword) is None
            for keyword in keywords
        )
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
        "fourteen": 14,
    }.get(value.casefold())
    if amount is None:
        amount = fixed_number(value)
    return amount if amount > 0 else None


def _fixed_creature_token_types(value: str) -> tuple[str, ...] | None:
    types = tuple(part.casefold() for part in value.split())
    if len(types) != len(set(types)) or not set(types).issubset(
        {"artifact", "enchantment", "land"}
    ):
        return None
    return tuple(
        card_type
        for card_type in ("artifact", "enchantment", "land")
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
        values = {field: value for field, value in definition.items() if field != "name"}
        if values.get("literal_token_name_version") == 1:
            token_name, characteristics = str(definition["name"]), values
        else:
            token_name, characteristics = predefined_token_identity(str(definition["name"]), values)
        return FixedTokenCreationTemplate(
            template_id=f"create-fixed-{name}-token-v1",
            effect={
                "op": "create_token",
                "controller": "$controller",
                "name": token_name,
                "quantity": quantity,
                **(
                    {"tapped": True}
                    if predefined.group("tapped")
                    else {}
                ),
                "characteristics": characteristics,
            },
            mechanics=(
                "cr-111-tokens",
                *(("fixed-named-token",) if definition.get("literal_token_name_version") == 1 else ()),
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
        *(("Land",) if "land" in card_types else ()),
        "Creature",
        *(("Enchantment",) if "enchantment" in card_types else ()),
    )
    characteristics: dict[str, Any] = {
        "type_line": f"Token {' '.join(ordered_types)} — {subtypes}",
        TOKEN_LITERAL_NAME_VERSION_FIELD: 1,
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
    if 'prowess' in keywords:
        characteristics.setdefault('ability_fragments', []).append(ability_fragment_to_dict(
            SpellCastKeywordTriggerSpec(kind=SpellCastKeywordTriggerKind.PROWESS)))
    if "decayed" in keywords:
        characteristics.setdefault("ability_fragments", []).extend(
            ability_fragment_to_dict(fragment)
            for fragment in decayed_ability_fragments()
        )
    for keyword in keywords:
        triggered = triggered_keyword_grant(keyword)
        if triggered is not None and keyword != 'prowess':
            characteristics.setdefault('ability_fragments', []).append(triggered[1])
        toxic = re.fullmatch(r"toxic (?P<value>[1-9]\d*)", keyword)
        if toxic is not None:
            characteristics.setdefault("ability_fragments", []).append(
                ability_fragment_to_dict(ToxicSpec(value=int(toxic["value"])))
            )
    return FixedTokenCreationTemplate(
        template_id="create-fixed-creature-token-v2",
        effect={
            "op": "create_token",
            "controller": "$controller",
            "name": fixed_ordinary_token_name(subtypes),
            "quantity": quantity,
            **({"tapped": True} if creature.group("tapped") else {}),
            "characteristics": characteristics,
        },
        mechanics=tuple(dict.fromkeys((
            "cr-111-tokens",
            *(keyword.split()[0] if keyword.startswith(("toxic ","bushido ")) else keyword for keyword in keywords),
        ))),
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
    if not normalized.casefold().startswith("create "):
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


def _created_token_haste_template(text):
    match = re.fullmatch(
        r"(?P<creation>Create .+?)\. (?P<subject>It gains|That token gains|They gain) haste until end of turn\.",
        " ".join(text.split()), re.I,
    )
    if match is None:
        return None
    base = _single_fixed_token_creation_effect_template(match["creation"] + ".")
    if base is None or base.target_schema is not None:
        return None
    from ..rules.token_creation_capability_shapes import created_token_haste_closure

    effect = {**base.effect, CREATED_TOKEN_HASTE_FIELD: 1}
    try:
        created_token_haste_closure(effect)
    except (TypeError, ValueError):
        return None
    singular_subject = match["subject"].casefold() != "they gain"
    if (base.effect["quantity"] == 1) != singular_subject:
        return None
    return FixedTokenCreationTemplate(base.template_id + "-then-haste-v1", effect, base.mechanics)


def fixed_token_creation_effect_template(
    text: str,
) -> FixedTokenCreationTemplate | None:
    """Lower one closed fixed token-definition instruction.

    A single fixed definition or an unambiguous two- or three-definition
    simultaneous nontargeted batch is accepted. Dynamic quantities,
    source-relative or modified copies, open ability text, attachments, and
    compound non-token instructions remain residual. Complete ordinary attacking
    riders use the closed public destination choice over the fixed definition.
    """

    from .attacking_token_results import fixed_attacking_token_template
    from .created_token_cleanup_templates import created_token_cleanup_template
    from .fixed_token_choices import fixed_token_choice_template

    explicit_creator = re.match(r"^You\s+create\s+", text.lstrip(), re.IGNORECASE)
    if explicit_creator is not None:
        text = "Create " + text.lstrip()[explicit_creator.end():]
    haste = _created_token_haste_template(text)
    if haste is not None:
        return haste
    cleanup = created_token_cleanup_template(text, compile_inner=fixed_token_creation_effect_template)
    from .per_opponent_attacking_tokens import per_opponent_attacking_token_template
    scoped = per_opponent_attacking_token_template(text, compile_attacking=lambda line:
        fixed_attacking_token_template(line, compile_fixed=_single_fixed_token_creation_effect_template))
    attacking = fixed_attacking_token_template(text, compile_fixed=_single_fixed_token_creation_effect_template)
    choice = fixed_token_choice_template(text, compile_fixed=_single_fixed_token_creation_effect_template)
    return cleanup or scoped or attacking or choice or _single_fixed_token_creation_effect_template(
        text
    ) or _fixed_token_batch_creation_effect_template(text)


def static_token_quantity_replacement_handler(text: str):
    """Compile the supported same-controller token quantity replacement."""
    from ..creature_token_definition_replacement import (
        CREATURE_TOKEN_TRIPLING_CAPABILITY, CREATURE_TOKEN_TRIPLING_HANDLER,
    )

    creature = (r"If one or more creature tokens would be created under your control, "
                r"three times that many of those tokens are created instead")
    if re.fullmatch(rf"{creature}\x2e?", text.strip(), re.IGNORECASE):
        return "fixed-creature-token-quantity-replacement-v1", {
            "handler_id": CREATURE_TOKEN_TRIPLING_HANDLER, "schema_version": 1, "event": "token.create",
            "condition": {"event_controller": "source_controller", "created_types": ["creature"]},
            "multiplier": 3,
        }, (CREATURE_TOKEN_TRIPLING_CAPABILITY,)
    current = r"If one or more tokens would be created under your control, twice that many of those tokens are created instead"
    legacy = r"If an effect would create one or more tokens under your control, it creates twice that many of those tokens instead"
    if re.fullmatch(f"(?:{current}|{legacy})\\.?", text.strip(), re.IGNORECASE) is None:
        return None
    return 'fixed-token-quantity-replacement-v1', {
        'handler_id':'replacement.token.quantity.v1','schema_version':1,'event':'token.create',
        'condition':{'event_controller':'source_controller'},'multiplier':2,
    }, ('token.creation.quantity_replacement',)


def _fixed_additional_token_definition(text: str) -> Mapping[str, Any] | None:
    """Reuse the complete fixed producer; reject unrepresented creation riders."""
    normalized=" ".join(text.casefold().split())
    if normalized in _PREDEFINED_CREATION_DEFINITIONS:
        definition = _TOKEN_DEFINITIONS[normalized]
        name, characteristics = predefined_token_identity(
            str(definition["name"]), {field: value for field, value in definition.items() if field != "name"},
        )
        if TOKEN_ABILITY_PROFILE_FIELD in characteristics:
            characteristics["ability_profile"] = characteristics.pop(TOKEN_ABILITY_PROFILE_FIELD)
        return {"name": name, **characteristics}
    template=_single_fixed_token_creation_effect_template('Create a '+text+'.')
    if template is None:return None
    effect=template.effect
    if (set(effect)!={'op','controller','name','quantity','characteristics'}
            or effect['op']!='create_token' or effect['controller']!='$controller'
            or type(effect['quantity']) is not int or effect['quantity']!=1):
        return None
    definition={'name':effect['name'],**effect['characteristics']}
    from ..semantic_runtime.token_replacements import TokenDefinition
    from ..semantic_runtime.context import SemanticNodeError
    try:TokenDefinition.from_mapping(definition).characteristics()
    except (SemanticNodeError,TypeError,ValueError):return None
    return definition


def static_creature_token_definition_replacement_handler(text: str):
    """Lower a fixed creature-token definition for the same creation quantity."""

    match = re.fullmatch(
        r"If one or more creature tokens would be created under your control, "
        r"that many (?P<definition>.+ creature tokens(?: with [^.]+)?) are created instead\.?",
        text.strip(), re.I,
    )
    if match is None:
        return None
    singular = re.sub(r"\btokens\b", "token", match["definition"], count=1, flags=re.I)
    definition = _fixed_additional_token_definition(singular)
    if definition is None:
        return None
    from ..characteristic_evaluation import type_parts

    if "creature" not in type_parts(str(definition["type_line"]))[0]:
        return None
    return ("controller-creature-token-definition-replacement-v1", {
        "handler_id": "replacement.token.creature-definition.v1", "schema_version": 1,
        "event": "token.create",
        "condition": {"event_controller": "source_controller", "created_types": ["creature"]},
        "definition": definition,
    }, ("token.creation.replace_creature_definition",))


def static_predefined_token_set_replacement_handler(text: str):
    """Compile the mandatory one-of-each substitution for predefined artifacts."""
    if re.fullmatch(
        r"If you would create a Clue, Food, or Treasure token, instead create one of each\.?",
        text.strip(), re.I,
    ) is None:
        return None
    from ..creature_token_definition_replacement import (
        PREDEFINED_TOKEN_SET_CAPABILITY, PREDEFINED_TOKEN_SET_HANDLER,
    )

    return ("controller-predefined-artifact-token-set-replacement-v1", {
        "handler_id": PREDEFINED_TOKEN_SET_HANDLER, "schema_version": 1,
        "event": "token.create", "event_controller": "source_controller",
    }, (PREDEFINED_TOKEN_SET_CAPABILITY,))


def static_token_creation_replacement_handler(text: str):
    return (
        static_token_quantity_replacement_handler(text)
        or static_creature_token_definition_replacement_handler(text)
        or static_predefined_token_set_replacement_handler(text)
    )


def _matching_pending_additional_tokens(text):
    match = re.fullmatch(
        r"If one or more tokens would be created under your control, "
        r"those tokens plus that many (?P<definition>.+ tokens) are created instead\.",
        text, re.I,
    )
    if match is None:
        return None
    definition = _fixed_additional_token_definition(match["definition"].removesuffix("s"))
    if definition is None:
        return None
    from ..replacement.operations import MATCH_PENDING_TOKEN_QUANTITY
    from ..semantic_runtime.token_replacements import (
        MATCH_PENDING_ADDITIONAL_TOKEN_CAPABILITY, MATCH_PENDING_ADDITIONAL_TOKEN_HANDLER_ID,
    )

    return ("additional-token-matching-pending-count-v1", {
        "handler_id": MATCH_PENDING_ADDITIONAL_TOKEN_HANDLER_ID,
        "schema_version": 3, "event": "token.create",
        "condition": {"event_controller": "source_controller", "created_types_all": [], "created_subtypes_all": []},
        "quantity": MATCH_PENDING_TOKEN_QUANTITY,
        "token": dict(definition),
    }, MATCH_PENDING_ADDITIONAL_TOKEN_CAPABILITY)


def static_additional_token_replacement_handler(
    text: str,
) -> tuple[str, Mapping[str, Any], str] | None:
    """Lower the closed mandatory fixed additional-token wording family."""

    normalized = text.strip()
    if matched := _matching_pending_additional_tokens(normalized):
        return matched
    match = _CREATOR_FRAME.fullmatch(normalized)
    if match is None:
        match = _CONTROLLED_FRAME.fullmatch(normalized)
    if match is None:
        return None

    quality = str(match.group("quality") or "").casefold()
    definition = _fixed_additional_token_definition(match.group("definition"))
    if definition is None:
        return None
    created_types = [quality] if quality in {'artifact','creature'} else []
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


def announced_token_sweep_template(text):
    from ..rules.announced_token_sweep import TOKEN_SWEEP_MECHANIC, announced_token_sweep_effect

    match = re.fullmatch(
        r"Create X (?P<token>[^.]+?) tokens\. If X is (?P<bound>[1-9][0-9]?) or more, "
        r"destroy all other creatures\.", " ".join(text.split()), re.I,
    )
    if match is None or int(match["bound"]) > 20:
        return None
    token = fixed_token_creation_effect_template(f"Create one {match['token']} token.")
    if token is None or "Creature" not in str(token.effect.get("characteristics", {}).get("type_line", "")).split():
        return None
    return ("cast-x-created-tokens-sweep-v1", (announced_token_sweep_effect(token.effect, int(match["bound"])),),
            None, (TOKEN_SWEEP_MECHANIC,))
