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
from .query_characteristic_templates import query_characteristic_quantity, query_characteristic_value
from .fixed_target_effect_sequences import FixedSourceCharacteristicsTemplate
from .declared_effect_amounts import (
    DECLARED_EFFECT_AMOUNT_MECHANIC,
    declared_amount_shape_context,
    declared_amount_dependencies,
)
from .scalar_effect_amounts import scalar_amount_shape_context
from .counter_placement_templates import FIXED_COUNTER_NAME_PATTERN
from ..scalar_effect_amount_model import SCALAR_AMOUNT_MECHANIC, SCALAR_AMOUNT_CAPABILITY


PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC = "public-query-effect-amount"
AMOUNT_MECHANIC_CAPABILITIES = {PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC: (PUBLIC_QUERY_AMOUNT_CAPABILITY,),
                               SCALAR_AMOUNT_MECHANIC: (SCALAR_AMOUNT_CAPABILITY,)}

CompiledEffectTemplate = tuple[
    str | None,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
]
FixedEffectCompiler = Callable[[str], CompiledEffectTemplate]

_NUMBER_WORDS = {
    "a": 1,
    "an": 1,
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
        r"(?P<verb>gain|gains|lose|loses) life equal to "
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
        r"^Draw cards equal to (?P<quantity>.+?)\.?$",
        re.IGNORECASE,
    ),
)
_TOKEN_PATTERN = re.compile(
    r"^Create X (?P<definition>.+? tokens?), where X is the number of "
    r"(?P<quantity>.+?)\.?$",
    re.IGNORECASE,
)
_TOKEN_FOR_EACH = re.compile(
    r"^Create (?P<coefficient>a|an|one|two|three|four|five|[1-9]\d*) "
    r"(?P<definition>.+? tokens?) for each (?P<quantity>.+?)\.?$",
    re.IGNORECASE,
)
_COUNTER_FOR_EACH = re.compile(
    rf"^Put (?P<coefficient>a|an|one|two|three|four|five|[1-9]\d*) "
    rf"(?P<counter>{FIXED_COUNTER_NAME_PATTERN}) counters? on "
    r"(?P<subject>.+?) for each (?P<quantity>.+?)\.?$", re.IGNORECASE,
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
            r"equal to (?P<quantity>.+?)\.?$",
            re.IGNORECASE,
        ),
        re.compile(
            rf"^(?P<source>{subject}) deals damage equal to "
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
) -> tuple[str, str, int, str, bool] | None:
    for index, pattern in enumerate(_LIFE_PATTERNS):
        match = pattern.fullmatch(text)
        if match is not None:
            return (
                "life",
                match.group("quantity"),
                _coefficient(match.groupdict().get("coefficient")),
                f"{match.group('subject') or ''}{match.group('verb')} 2 life.",
                index == 0,
            )
    for index, pattern in enumerate(_damage_patterns(source_name)):
        match = pattern.fullmatch(text)
        if match is not None:
            return (
                "damage",
                match.group("quantity"),
                _coefficient(match.groupdict().get("coefficient")),
                f"{match.group('source')} deals 2 damage "
                f"{match.group('recipient')}.",
                index < 2,
            )
    for index, pattern in enumerate(_DRAW_PATTERNS):
        match = pattern.fullmatch(text)
        if match is not None:
            return "draw", match.group("quantity"), 1, "Draw two cards.", index == 1
    token = _TOKEN_PATTERN.fullmatch(text) or _TOKEN_FOR_EACH.fullmatch(text)
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
            _coefficient(token.groupdict().get("coefficient")),
            f"Create two {definition}.",
            False,
        )
    counter = _COUNTER_FOR_EACH.fullmatch(text)
    if counter is not None:
        return ('counter', counter['quantity'], _coefficient(counter['coefficient']),
            f"Put two {counter['counter']} counters on {counter['subject']}.", False)
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
    "place_counters": "amount",
}


def _source_characteristic_amount_base(
    effects: Sequence[Mapping[str, Any]],
) -> FixedSourceCharacteristicsTemplate | None:
    """Consume the entire fixed self result before adapting its scalar."""
    modifiers = [
        effect for effect in effects
        if effect.get("op") == "modify_stats_until_end_of_turn"
    ]
    if len(modifiers) != 1 or not 1 <= len(effects) <= 3:
        return None
    keywords = []
    for effect in effects:
        if effect.get("card") != "$source":
            return None
        if effect.get("op") == "modify_stats_until_end_of_turn":
            if set(effect) != {"op", "card", "power", "toughness"} or any(
                type(effect.get(field)) is not int
                for field in ("power", "toughness")
            ):
                return None
        elif effect.get("op") == "grant_keyword_until_end_of_turn":
            if set(effect) != {"op", "card", "keyword"} or type(effect["keyword"]) is not str:
                return None
            keywords.append(effect["keyword"])
        else:
            return None
    try:
        return FixedSourceCharacteristicsTemplate(
            source_kind="query-stat-modifier",
            power=modifiers[0]["power"], toughness=modifiers[0]["toughness"],
            keywords=tuple(keywords),
        )
    except ValueError:
        return None


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
        if template_id is None or not mechanics:
            return None
        if target_schema is None and effects and all(effect.get("card") == "$source" for effect in effects):
            source_template = _source_characteristic_amount_base(effects)
            if source_template is None:
                return None
            template_id, effects, target_schema, mechanics = source_template.compiled()
        modified = 0
        projected: list[Mapping[str, Any]] = []
        for raw_effect in effects:
            effect = deepcopy(dict(raw_effect))
            if effect.get("op") in {"modify_stats_until_end_of_turn", "apply_source_characteristics_until_end_of_turn"}:
                if (
                    type(effect.get("power")) is not int
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
    family, quantity_text, coefficient, fixed_text, complete_value = candidate
    quantity = (
        query_characteristic_value(quantity_text, source_name=source_name)
        if complete_value else query_characteristic_quantity(
            quantity_text, source_name=source_name, definition_extensions=True,
        )
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
        if value.get("op") in {"modify_stats_until_end_of_turn", "apply_source_characteristics_until_end_of_turn"}:
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
        effect.get("op") in {"modify_stats_until_end_of_turn", "apply_source_characteristics_until_end_of_turn"}
        and contains_public_query_effect_amount(effect)
        for effect in projected
    )
    if characteristic:
        modifiers = tuple(
            effect
            for effect in projected
            if effect.get("op") in {"modify_stats_until_end_of_turn", "apply_source_characteristics_until_end_of_turn"}
        )
        references = {
            effect.get("card")
            for effect in projected
            if effect.get("op")
            in {
                "grant_keyword_until_end_of_turn",
                "modify_stats_until_end_of_turn",
                "apply_source_characteristics_until_end_of_turn",
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
                    "apply_source_characteristics_until_end_of_turn",
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
        if operation in {"modify_stats_until_end_of_turn", "apply_source_characteristics_until_end_of_turn"}:
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
                if spec.schema_version != 1:
                    return None
            except PublicQueryAmountError:
                return None
            effect[field] = -1 if spec.coefficient < 0 else 1
            descriptor_count += 1
    if descriptor_count < 1 or any(
        contains_public_query_effect_amount(effect) for effect in projected
    ):
        return None
    return tuple(projected)


def _composed_query_shape_context(effects, mechanics):
    if not {'closed-effect-program', 'bound-effect-program'}.intersection(mechanics):
        return None
    projected = []
    for effect in effects:
        if contains_public_query_effect_amount(effect):
            fixed = _fixed_shape_effects((effect,))
            if fixed is None:
                return None
            projected.extend(fixed)
        else:
            projected.append(deepcopy(dict(effect)))
    return tuple(projected), mechanics - {PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC}


def public_query_amount_program_is_closed(program: Any, *, required_dependencies) -> bool:
    """Keep scalar admission with its existing fixed shape and target owners."""
    from ..rules.node_capability_shapes import (
        fixed_damage_node_capabilities, fixed_draw_node_capabilities,
        fixed_source_characteristics_node_capabilities,
        fixed_target_characteristics_node_capabilities,
    )
    from ..rules.fixed_controller_effect_shapes import fixed_life_node_capabilities
    from ..rules.token_creation_capability_shapes import fixed_token_creation_node_capabilities
    from ..rules.counter_placement_capability_shapes import fixed_counter_placement_node_capabilities

    if not {PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC, SCALAR_AMOUNT_MECHANIC}.intersection(program.coverage):
        return False
    context = public_query_amount_shape_context(program.effects, set(program.coverage))
    if context is None:
        return False
    effects, mechanics = context
    required = set(required_dependencies)
    if (
        (SCALAR_AMOUNT_CAPABILITY if SCALAR_AMOUNT_MECHANIC in program.coverage else PUBLIC_QUERY_AMOUNT_CAPABILITY) not in required
        or not required.issubset(program.capability_dependencies)
    ):
        return False
    return any(
        resolver(effects=effects, target_schema=program.target_schema, mechanic_ids=mechanics)
        for resolver in (
            fixed_damage_node_capabilities,
            fixed_draw_node_capabilities,
            fixed_life_node_capabilities,
            fixed_token_creation_node_capabilities,
            fixed_target_characteristics_node_capabilities,
            fixed_source_characteristics_node_capabilities,
            fixed_counter_placement_node_capabilities,
        )
    )


def public_query_amount_shape_context(
    effects: Sequence[Mapping[str, Any]], mechanics: set[str]
) -> tuple[tuple[Mapping[str, Any], ...], set[str]] | None:
    """Return fixed-value inputs for existing capability shape owners."""

    if SCALAR_AMOUNT_MECHANIC in mechanics:
        return scalar_amount_shape_context(effects, mechanics)
    if DECLARED_EFFECT_AMOUNT_MECHANIC in mechanics:
        return declared_amount_shape_context(effects, mechanics)
    if PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC not in mechanics:
        return tuple(effects), set(mechanics)
    if len(effects) > 1 and (composed := _composed_query_shape_context(effects, mechanics)) is not None:
        return composed
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
