from __future__ import annotations

from copy import deepcopy
import re
from typing import Callable, Mapping, Sequence, Any

from ..query_effect_amount_model import (
    PUBLIC_QUERY_AMOUNT_CAPABILITY,
    PUBLIC_QUERY_AMOUNT_KIND,
    PublicQueryAmountError,
    PublicQueryAmountSpec,
)
from ..rules.source_references import SourceReferenceSpec
from .query_characteristic_templates import query_characteristic_quantity


PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC = "public-query-effect-amount"

CompiledEffectTemplate = tuple[
    str | None,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
]
FixedEffectCompiler = Callable[[str], CompiledEffectTemplate]

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
}
_TRAILING_REMINDER = re.compile(
    r"\s+\([^()]*(?:\([^()]*\)[^()]*)*\)\.?$"
)
_LIFE_PATTERNS = (
    re.compile(
        r"^(?P<subject>(?:you|target player|target opponent|each opponent) )?"
        r"(?P<verb>gain|gains|lose|loses) life equal to the number of "
        r"(?P<quantity>.+?)\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<subject>(?:you|target player|target opponent|each opponent) )?"
        r"(?P<verb>gain|gains|lose|loses) "
        r"(?P<coefficient>[1-9]\d*|one|two|three|four|five) life for each "
        r"(?P<quantity>.+?)\.?$",
        re.IGNORECASE,
    ),
)
_DRAW_PATTERNS = (
    re.compile(
        r"^Draw a card for each (?P<quantity>.+?)\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^Draw cards equal to the number of (?P<quantity>.+?)\.?$",
        re.IGNORECASE,
    ),
)
_TOKEN_PATTERN = re.compile(
    r"^Create X (?P<definition>.+? tokens?), where X is the number of "
    r"(?P<quantity>.+?)\.?$",
    re.IGNORECASE,
)
_QUERY_CHARACTERISTIC_PATTERNS = (
    (
        "where-x",
        re.compile(
            r"^(?P<subject>.+?) "
            r"(?:(?:gains? (?P<prefix_keywords>.+?) and )?gets) "
            r"(?P<power>[+-](?:X|0))/(?P<toughness>[+-](?:X|0))"
            r"(?P<suffix_keywords> and gains? .+?)? until end of turn, "
            r"where X is (?:(?:equal to )?the number of )?"
            r"(?P<quantity>.+?)\.?$",
            re.IGNORECASE,
        ),
    ),
    (
        "per-object",
        re.compile(
            r"^(?P<subject>.+?) "
            r"(?:(?:gains? (?P<prefix_keywords>.+?) and )?gets) "
            r"(?P<power>[+-]\d+)/(?P<toughness>[+-]\d+)"
            r"(?P<suffix_keywords> and gains? .+?)? until end of turn "
            r"for each (?P<quantity>.+?)\.?$",
            re.IGNORECASE,
        ),
    ),
    (
        "per-object",
        re.compile(
            r"^(?P<subject>.+?) "
            r"(?:(?:gains? (?P<prefix_keywords>.+?) and )?gets) "
            r"(?P<power>[+-]\d+)/(?P<toughness>[+-]\d+)"
            r"(?P<suffix_keywords> and gains? .+?)? for each "
            r"(?P<quantity>.+?) until end of turn\.?$",
            re.IGNORECASE,
        ),
    ),
)


def _coefficient(value: str | None) -> int:
    if value is None:
        return 1
    normalized = value.casefold()
    return int(normalized) if normalized.isdigit() else _NUMBER_WORDS[normalized]


def _damage_patterns(source_name: str) -> tuple[re.Pattern[str], ...]:
    source = SourceReferenceSpec(source_name).regex_pattern
    subject = rf"(?:{source}|it|this (?:artifact|creature|enchantment|permanent))"
    return (
        re.compile(
            rf"^(?P<source>{subject}) deals damage (?P<recipient>.+?) "
            r"equal to the number of (?P<quantity>.+?)\.?$",
            re.IGNORECASE,
        ),
        re.compile(
            rf"^(?P<source>{subject}) deals damage equal to the number of "
            r"(?P<quantity>.+?) to (?P<recipient>.+?)\.?$",
            re.IGNORECASE,
        ),
        re.compile(
            rf"^(?P<source>{subject}) deals "
            r"(?P<coefficient>[1-9]\d*|one|two|three|four|five) damage "
            r"(?P<recipient>.+?) for each (?P<quantity>.+?)\.?$",
            re.IGNORECASE,
        ),
    )


def _parsed_candidate(
    text: str,
    *,
    source_name: str,
) -> tuple[str, str, int, str] | None:
    for pattern in _LIFE_PATTERNS:
        match = pattern.fullmatch(text)
        if match is not None:
            return (
                "life",
                match.group("quantity"),
                _coefficient(match.groupdict().get("coefficient")),
                f"{match.group('subject') or ''}{match.group('verb')} 2 life.",
            )
    for pattern in _damage_patterns(source_name):
        match = pattern.fullmatch(text)
        if match is not None:
            return (
                "damage",
                match.group("quantity"),
                _coefficient(match.groupdict().get("coefficient")),
                f"{match.group('source')} deals 2 damage "
                f"{match.group('recipient')}.",
            )
    for pattern in _DRAW_PATTERNS:
        match = pattern.fullmatch(text)
        if match is not None:
            return "draw", match.group("quantity"), 1, "Draw two cards."
    token = _TOKEN_PATTERN.fullmatch(text)
    if token is not None:
        definition = re.sub(
            r" token$",
            " tokens",
            token.group("definition"),
            flags=re.IGNORECASE,
        )
        return (
            "token",
            token.group("quantity"),
            1,
            f"Create two {definition}.",
        )
    return None


def _query_characteristic_candidate(
    text: str,
    *,
    source_name: str,
) -> tuple[str, str, int, int, str] | None:
    """Parse one query-scaled target or source layer-7c modifier."""

    for calculation, pattern in _QUERY_CHARACTERISTIC_PATTERNS:
        match = pattern.fullmatch(text)
        if match is None:
            continue
        prefix_keywords = match.group("prefix_keywords")
        suffix_keywords = match.group("suffix_keywords")
        if prefix_keywords is not None and suffix_keywords is not None:
            return None
        subject = match.group("subject")
        source = SourceReferenceSpec(source_name)
        source_subject = subject.casefold() in {
            "it",
            "this artifact",
            "this creature",
            "this enchantment",
            "this permanent",
        } or re.fullmatch(source.regex_pattern, subject, re.IGNORECASE)
        fixed_subject = "this permanent" if source_subject else subject

        coefficients: list[int] = []
        for field in ("power", "toughness"):
            raw = match.group(field).upper()
            if raw in {"+0", "-0"}:
                coefficient = 0
            elif calculation == "where-x":
                coefficient = 1 if raw.startswith("+") else -1
            else:
                coefficient = int(raw)
            coefficients.append(coefficient)
        if coefficients == [0, 0]:
            return None

        keyword_suffix = suffix_keywords or (
            f" and gains {prefix_keywords}" if prefix_keywords else ""
        )
        return (
            fixed_subject,
            keyword_suffix,
            coefficients[0],
            coefficients[1],
            match.group("quantity"),
        )
    return None


_AMOUNT_FIELDS = {
    "life": "delta",
    "lose_life": "amount",
    "lose_life_each_opponent": "amount",
    "damage": "amount",
    "draw": "count",
    "create_token": "quantity",
}


def public_query_effect_amount_template(
    text: str,
    *,
    source_name: str,
    compile_fixed: FixedEffectCompiler,
) -> CompiledEffectTemplate | None:
    """Lower one standalone query-derived amount onto an existing fixed op."""

    normalized = _TRAILING_REMINDER.sub("", text.strip()).strip()
    candidate = _parsed_candidate(normalized, source_name=source_name)
    characteristic = _query_characteristic_candidate(
        normalized,
        source_name=source_name,
    )
    if candidate is None and characteristic is None:
        return None
    if characteristic is not None:
        subject, keyword_suffix, power, toughness, quantity_text = (
            characteristic
        )
        quantity = query_characteristic_quantity(
            quantity_text,
            source_name=source_name,
            definition_extensions=True,
        )
        if quantity is None:
            return None
        try:
            amounts = {
                field: (
                    PublicQueryAmountSpec(
                        quantity=quantity,
                        coefficient=coefficient,
                    ).to_dict()
                    if coefficient
                    else 0
                )
                for field, coefficient in (
                    ("power", power),
                    ("toughness", toughness),
                )
            }
        except PublicQueryAmountError:
            return None
        fixed_text = (
            f"{subject} gets {power * 2:+d}/{toughness * 2:+d}"
            f"{keyword_suffix} until end of turn."
        )
        template_id, effects, target_schema, mechanics = compile_fixed(
            fixed_text
        )
        modified = 0
        projected: list[Mapping[str, Any]] = []
        for raw_effect in effects:
            effect = deepcopy(dict(raw_effect))
            if effect.get("op") == "modify_stats_until_end_of_turn":
                if (
                    set(effect) != {"op", "card", "power", "toughness"}
                    or type(effect.get("power")) is not int
                    or type(effect.get("toughness")) is not int
                ):
                    return None
                effect.update(amounts)
                modified += 1
            projected.append(effect)
        if template_id is None or modified != 1 or not mechanics:
            return None
        return (
            "public-query-characteristic-amount-v1",
            tuple(projected),
            deepcopy(target_schema),
            tuple(
                dict.fromkeys(
                    (PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC, *mechanics)
                )
            ),
        )
    assert candidate is not None
    family, quantity_text, coefficient, fixed_text = candidate
    quantity = query_characteristic_quantity(
        quantity_text,
        source_name=source_name,
        definition_extensions=True,
    )
    if quantity is None:
        return None
    try:
        amount = PublicQueryAmountSpec(quantity=quantity, coefficient=coefficient)
    except PublicQueryAmountError:
        return None
    template_id, effects, target_schema, mechanics = compile_fixed(fixed_text)
    if template_id is None or len(effects) != 1 or not mechanics:
        return None
    effect = deepcopy(dict(effects[0]))
    operation = str(effect.get("op") or "")
    field = _AMOUNT_FIELDS.get(operation)
    if field is None or type(effect.get(field)) is not int:
        return None
    sample = int(effect[field])
    if abs(sample) != 2:
        return None
    signed_coefficient = coefficient * (-1 if sample < 0 else 1)
    if field != "delta" and signed_coefficient < 0:
        return None
    effect[field] = PublicQueryAmountSpec(
        quantity=amount.quantity,
        coefficient=signed_coefficient,
    ).to_dict()
    return (
        f"public-query-{family}-amount-v1",
        (effect,),
        deepcopy(target_schema),
        tuple(dict.fromkeys((PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC, *mechanics))),
    )


def contains_public_query_effect_amount(value: Any) -> bool:
    """Return whether nested semantic data contains this scalar descriptor."""

    if isinstance(value, Mapping):
        return value.get("kind") == PUBLIC_QUERY_AMOUNT_KIND or any(
            contains_public_query_effect_amount(child) for child in value.values()
        )
    return isinstance(value, (list, tuple)) and any(
        contains_public_query_effect_amount(child) for child in value
    )


def contains_public_query_characteristic_amount(value: Any) -> bool:
    """Return whether one temporary stat operation carries this scalar."""

    if isinstance(value, Mapping):
        if value.get("op") == "modify_stats_until_end_of_turn":
            return any(
                isinstance(value.get(field), Mapping)
                and value[field].get("kind") == PUBLIC_QUERY_AMOUNT_KIND
                for field in ("power", "toughness")
            )
        return any(
            contains_public_query_characteristic_amount(child)
            for child in value.values()
        )
    return isinstance(value, (list, tuple)) and any(
        contains_public_query_characteristic_amount(child)
        for child in value
    )


def _fixed_shape_effects(
    effects: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...] | None:
    """Project query-derived scalars to positive fixed values for shape checks."""

    projected = [deepcopy(dict(effect)) for effect in effects]
    characteristic = any(
        effect.get("op") == "modify_stats_until_end_of_turn"
        and contains_public_query_effect_amount(effect)
        for effect in projected
    )
    if characteristic:
        modifiers = tuple(
            effect
            for effect in projected
            if effect.get("op") == "modify_stats_until_end_of_turn"
        )
        references = {
            effect.get("card")
            for effect in projected
            if effect.get("op")
            in {
                "grant_keyword_until_end_of_turn",
                "modify_stats_until_end_of_turn",
            }
        }
        if (
            not 1 <= len(projected) <= 3
            or len(modifiers) != 1
            or len(references) != 1
            or any(
                effect.get("op")
                not in {
                    "grant_keyword_until_end_of_turn",
                    "modify_stats_until_end_of_turn",
                }
                for effect in projected
            )
        ):
            return None
    elif len(projected) != 1:
        return None
    descriptor_count = 0
    for effect in projected:
        operation = str(effect.get("op") or "")
        fields: tuple[str, ...]
        if operation == "modify_stats_until_end_of_turn":
            fields = ("power", "toughness")
        else:
            field = _AMOUNT_FIELDS.get(operation)
            fields = (field,) if field is not None else ()
        for field in fields:
            value = effect.get(field)
            if (
                not isinstance(value, Mapping)
                or value.get("kind") != PUBLIC_QUERY_AMOUNT_KIND
            ):
                continue
            try:
                spec = PublicQueryAmountSpec.from_dict(value)
            except PublicQueryAmountError:
                return None
            effect[field] = -1 if spec.coefficient < 0 else 1
            descriptor_count += 1
    if descriptor_count < 1 or any(
        contains_public_query_effect_amount(effect) for effect in projected
    ):
        return None
    return tuple(projected)


def public_query_amount_shape_context(
    effects: Sequence[Mapping[str, Any]], mechanics: set[str]
) -> tuple[tuple[Mapping[str, Any], ...], set[str]] | None:
    """Return fixed-value inputs for existing capability shape owners."""

    if PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC not in mechanics:
        return tuple(effects), set(mechanics)
    projected = _fixed_shape_effects(effects)
    if projected is None:
        return None
    return projected, mechanics - {PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC}


__all__ = [
    "PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC",
    "PUBLIC_QUERY_AMOUNT_CAPABILITY",
    "contains_public_query_characteristic_amount",
    "contains_public_query_effect_amount",
    "public_query_amount_shape_context",
    "public_query_effect_amount_template",
]
