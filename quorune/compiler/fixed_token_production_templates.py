from __future__ import annotations

"""Closed extensions for fixed token production across effect contexts."""

import re
from typing import Callable

from ..ability_fragments import ability_fragment_to_dict
from ..declaration_restrictions import parse_declaration_restriction_line
from ..fixed_token_production import (
    FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
    FixedInvestigateSpec,
    FixedTokenCreationTemplate,
    fixed_token_player_target_schema,
    FixedTokenProductionError,
    INVESTIGATE_MECHANIC_ID,
    TOKEN_LITERAL_NAME_VERSION_FIELD,
    fixed_ordinary_token_name,
)
from .direct_target import direct_permanent_target_spec
from .fixed_numbers import fixed_number


_COLOR_SYMBOLS = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
}
_COLOR_ORDER = "WUBRG"
_FIXED_INVESTIGATE = re.compile(
    r"^Investigate(?: (?P<count>one|two|three|four|five|six|seven|eight|"
    r"nine|ten|\d+) times)?\.?$",
    re.IGNORECASE,
)
_FIXED_COPY_TOKEN = re.compile(
    r"^Create a token that's a copy of (?P<subject>target (?:creature|"
    r"artifact, creature, or land))\.?$",
    re.IGNORECASE,
)
_FIXED_DELAYED_TOKEN = re.compile(
    r"^(?P<body>Create .+) "
    r"at the beginning of the next end step\.?$",
    re.IGNORECASE,
)
_FIXED_TARGETED_CREATOR = re.compile(
    r"Target (?P<relation>opponent|player) creates (?P<body>.+)",
    re.IGNORECASE,
)
_FIXED_NAMED_TOKEN = re.compile(
    r"^(?P<body>Create .+?) named "
    r"(?P<name>[A-Za-z][A-Za-z' -]*)\.?$", re.IGNORECASE,
)
_FIXED_LEGENDARY_NAMED_TOKEN = re.compile(
    r"^Create (?P<name>[A-Za-z][A-Za-z' -]*), a legendary "
    r"(?P<body>\d+/\d+ .+? creature token)\.?$", re.IGNORECASE,
)
_FIXED_POSTPOSED_COLOR_TOKEN = re.compile(
    r"^Create (?P<count>a|an|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"\d+) (?P<power>\d+)/(?P<toughness>\d+) "
    r"(?P<subtypes>[A-Za-z][A-Za-z' -]*?) creature tokens? "
    r"(?:that's|that are) (?P<colors>[A-Za-z, ]+)\.?$",
    re.IGNORECASE,
)
_FIXED_TOKEN_DECLARATION = re.compile(
    r'^(?P<body>Create .+? creature tokens?(?: with [A-Za-z0-9 ,\'-]+)?) (?:with|and) "(?P<ability>'
    r"This token can't (?:block|be blocked)\.)\"\.?$",
)


def _positive_fixed_number(value: str) -> int | None:
    amount = fixed_number(value)
    return amount if amount > 0 else None


def _fixed_color_words(value: str) -> list[str] | None:
    words = tuple(
        part.strip().casefold()
        for part in re.sub(r",?\s+and\s+", ",", value).split(",")
        if part.strip()
    )
    if (
        not 1 <= len(words) <= 3
        or len(words) != len(set(words))
        or any(word not in _COLOR_SYMBOLS for word in words)
    ):
        return None
    return sorted(
        (_COLOR_SYMBOLS[word] for word in words),
        key=_COLOR_ORDER.index,
    )


def _copy_template(
    template: FixedTokenCreationTemplate,
    *,
    template_id: str,
    effect: dict[str, object],
    mechanics: tuple[str, ...] = (),
) -> FixedTokenCreationTemplate:
    return FixedTokenCreationTemplate(
        template_id=template_id,
        effect=effect,
        mechanics=tuple(dict.fromkeys((*template.mechanics, *mechanics))),
        target_schema=template.target_schema,
    )


def _fixed_investigate_template(
    normalized: str,
) -> FixedTokenCreationTemplate | None:
    match = _FIXED_INVESTIGATE.fullmatch(normalized)
    if match is None:
        return None
    count_text = match.group("count")
    count = 1 if count_text is None else _positive_fixed_number(count_text)
    if count is None:
        return None
    try:
        spec = FixedInvestigateSpec(count)
    except FixedTokenProductionError:
        return None
    return FixedTokenCreationTemplate(
        template_id="investigate-fixed-v1",
        effect=spec.effect(),
        mechanics=(
            INVESTIGATE_MECHANIC_ID,
            FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
            "cr-111-tokens",
        ),
    )


def _fixed_copy_token_template(
    normalized: str,
) -> FixedTokenCreationTemplate | None:
    match = _FIXED_COPY_TOKEN.fullmatch(normalized)
    if match is None:
        return None
    spec = direct_permanent_target_spec(match.group("subject"))
    if spec is None:
        return None
    return FixedTokenCreationTemplate(
        template_id="create-fixed-target-copy-token-v1",
        effect={
            "op": "create_token",
            "controller": "$controller",
            "quantity": 1,
            "copy_of": "$target.0",
        },
        target_schema=spec.to_target_schema(),
        mechanics=(
            FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
            "fixed-token-copy",
            "cr-111-tokens",
            "cr-115-targets",
            "cr-707-copying-objects",
        ),
    )


def _fixed_delayed_token_template(
    normalized: str,
    *,
    compile_inner: Callable[[str], FixedTokenCreationTemplate | None],
) -> FixedTokenCreationTemplate | None:
    match = _FIXED_DELAYED_TOKEN.fullmatch(normalized)
    if match is None:
        return None
    inner = compile_inner(match.group("body") + ".")
    if (
        inner is None
        or inner.target_schema is not None
        or inner.effect.get("op") != "create_token"
    ):
        return None
    label = "Create fixed token at the beginning of the next end step"
    return FixedTokenCreationTemplate(
        template_id="create-fixed-token-next-end-step-v1",
        effect={
            "op": "delayed_trigger",
            "controller": "$controller",
            "label": label,
            "event": "step.begin",
            "condition": {"phase": "ending", "step": "end_step"},
            "stack": {
                "label": label,
                "context": {"dynamic_effects": [dict(inner.effect)]},
            },
            "once": True,
        },
        mechanics=tuple(
            dict.fromkeys(
                (
                    *inner.mechanics,
                    FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
                    "fixed-delayed-token-creation",
                    "cr-603-handling-triggered-abilities",
                )
            )
        ),
    )


def _fixed_declaration_token_template(
    normalized: str,
    *,
    compile_inner: Callable[[str], FixedTokenCreationTemplate | None],
) -> FixedTokenCreationTemplate | None:
    match = _FIXED_TOKEN_DECLARATION.fullmatch(normalized)
    if match is None:
        return None
    parsed = parse_declaration_restriction_line(
        match.group("ability").replace("This token", "This creature"),
        card_name="Token",
    )
    if (
        not parsed.exact
        or parsed.template is None
        or parsed.template.template_id
        not in {"intrinsic-block-prohibition-v1", "intrinsic-unblockable-v1"}
    ):
        return None
    inner = compile_inner(match.group("body") + ".")
    if inner is None or inner.target_schema is not None:
        return None
    characteristics = dict(inner.effect.get("characteristics") or {})
    characteristics["ability_fragments"] = [
        *characteristics.get("ability_fragments", []),
        ability_fragment_to_dict(parsed.template)
    ]
    return _copy_template(
        inner,
        template_id="create-fixed-declaration-creature-token-v1",
        effect={**dict(inner.effect), "characteristics": characteristics},
        mechanics=(
            FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
            "fixed-token-declaration-fragment",
            *parsed.template.mechanics,
        ),
    )


def _fixed_targeted_creator_template(
    normalized: str,
    *,
    compile_inner: Callable[[str], FixedTokenCreationTemplate | None],
) -> FixedTokenCreationTemplate | None:
    match = _FIXED_TARGETED_CREATOR.fullmatch(normalized)
    if match is None:
        return None
    definition = compile_inner("Create " + match["body"])
    if (
        definition is None
        or definition.target_schema is not None
        or definition.effect.get("op") != "create_token"
        or definition.effect.get("controller") != "$controller"
        or {"copy_of", "copy_spec"} & definition.effect.keys()
    ):
        return None
    relation = "opponent" if match["relation"].casefold() == "opponent" else "any"
    effect = dict(definition.effect)
    del effect["controller"]
    effect["target"] = "$target.0"
    return FixedTokenCreationTemplate(
        template_id=f"create-fixed-token-for-target-{match['relation'].casefold()}-v1",
        effect=effect,
        mechanics=(*definition.mechanics, "cr-115-targets"),
        target_schema=fixed_token_player_target_schema(relation),
    )


def _fixed_named_token_template(
    normalized: str,
    *,
    compile_inner: Callable[[str], FixedTokenCreationTemplate | None],
) -> FixedTokenCreationTemplate | None:
    legendary = _FIXED_LEGENDARY_NAMED_TOKEN.fullmatch(normalized)
    if legendary is not None:
        inner = compile_inner("Create a " + legendary.group("body") + ".")
        if inner is None or inner.target_schema is not None:
            return None
        characteristics = dict(inner.effect.get("characteristics") or {})
        type_line = str(characteristics.get("type_line") or "")
        if not type_line.startswith("Token Creature"):
            return None
        characteristics["type_line"] = type_line.replace(
            "Token Creature", "Token Legendary Creature", 1
        )
        return _copy_template(
            inner,
            template_id="create-fixed-named-legendary-creature-token-v1",
            effect={
                **dict(inner.effect),
                "name": legendary.group("name").strip(),
                "characteristics": characteristics,
            },
            mechanics=(
                FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
                "fixed-named-token",
            ),
        )
    named = _FIXED_NAMED_TOKEN.fullmatch(normalized)
    if named is None:
        return None
    if re.search(r"\b(?:that's|that is|that are)\b", named.group('name'), re.IGNORECASE):
        return None
    inner = compile_inner(named.group("body") + ".")
    if inner is None or inner.target_schema is not None:
        return None
    return _copy_template(
        inner,
        template_id="create-fixed-named-creature-token-v1",
        effect={**dict(inner.effect), "name": named.group("name").strip()},
        mechanics=(
            FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
            "fixed-named-token",
        ),
    )


def _fixed_postposed_color_token_template(
    normalized: str,
) -> FixedTokenCreationTemplate | None:
    match = _FIXED_POSTPOSED_COLOR_TOKEN.fullmatch(normalized)
    if match is None:
        return None
    quantity = _positive_fixed_number(match.group("count"))
    colors = _fixed_color_words(match.group("colors"))
    if quantity is None or colors is None:
        return None
    subtypes = " ".join(match.group("subtypes").split())
    return FixedTokenCreationTemplate(
        template_id="create-fixed-postposed-color-creature-token-v1",
        effect={
            "op": "create_token",
            "controller": "$controller",
            "name": fixed_ordinary_token_name(subtypes),
            "quantity": quantity,
            "characteristics": {
                "type_line": f"Token Creature — {subtypes}",
                TOKEN_LITERAL_NAME_VERSION_FIELD: 1,
                "colors": colors,
                "power": match.group("power"),
                "toughness": match.group("toughness"),
            },
        },
        mechanics=(FIXED_TOKEN_PRODUCTION_MECHANIC_ID, "cr-111-tokens"),
    )


def extended_fixed_token_creation_effect_template(
    normalized: str,
    *,
    compile_inner: Callable[[str], FixedTokenCreationTemplate | None],
) -> FixedTokenCreationTemplate | None:
    """Lower one extended form or return ``None`` to the base grammar."""

    for compiler in (
        _fixed_investigate_template,
        _fixed_copy_token_template,
        _fixed_postposed_color_token_template,
    ):
        compiled = compiler(normalized)
        if compiled is not None:
            return compiled
    for compiler in (
        _fixed_delayed_token_template,
        _fixed_declaration_token_template,
        _fixed_named_token_template,
        _fixed_targeted_creator_template,
    ):
        compiled = compiler(normalized, compile_inner=compile_inner)
        if compiled is not None:
            return compiled
    return None


__all__ = ["extended_fixed_token_creation_effect_template"]
