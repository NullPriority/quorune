from __future__ import annotations

"""Closed Oracle grammar for fixed controller spell-cost reductions."""

import re
from typing import Any, Mapping

from ..creature_subtypes import canonical_creature_subtype
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..object_predicate import PermanentStatePredicateSpec
from ..self_cast_reductions import (
    CastReductionMetric,
    CastReductionMetricKind,
    CastReductionObjectQuery,
    CastReductionQueryScope,
    CastReductionTurnFact,
    SelfSpellCostReductionSpec,
    SelfSpellCostReductionTerm,
)
from ..semantic_runtime.cast_costs import (
    FIXED_SPELL_COST_REDUCTION_CAPABILITY_ID,
    FIXED_SPELL_COST_REDUCTION_EVENT,
    FIXED_SPELL_COST_REDUCTION_HANDLER_ID,
    SELF_SPELL_COST_REDUCTION_CAPABILITY_ID,
    SELF_SPELL_COST_REDUCTION_HANDLER_ID,
)


CastCostModifierTemplate = tuple[
    str,
    Mapping[str, Any],
    str,
]


_FIXED_GENERIC_SPELL_REDUCTION = re.compile(
    r"^(?P<subject>.+?) cost(?:s)? "
    r"\{(?P<amount>[1-9][0-9]*)\} less to cast\.?$",
    re.IGNORECASE,
)
_CARD_TYPES = frozenset(
    {
        "artifact",
        "battle",
        "creature",
        "enchantment",
        "instant",
        "land",
        "planeswalker",
        "sorcery",
    }
)
_COLORS = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
}
_SUPERTYPES = frozenset({"legendary", "snow"})
_NONCREATURE_SUBTYPES = frozenset(
    {"arcane", "aura", "cave", "equipment", "lesson", "saga", "vehicle"}
)

_SELF_REDUCTION = re.compile(
    r"^This spell costs (?P<amount>\{(?:[1-9][0-9]*|[WUBRGC])\}) "
    r"less to cast (?P<metric>.+?)\.?$",
    re.IGNORECASE,
)
_SELF_VARIABLE_REDUCTION = re.compile(
    r"^This spell costs \{X\} less to cast, where X is "
    r"(?P<metric>.+?)\.?$",
    re.IGNORECASE,
)
_DOMAIN_REDUCTION = re.compile(
    r"^Domain — This spell costs (?P<amount>\{[1-9][0-9]*\}) less to cast "
    r"for each basic land type among lands you control\.?$",
    re.IGNORECASE,
)
_DOUBLE_PUBLIC_REDUCTION = re.compile(
    r"^This spell costs (?P<first>\{[1-9][0-9]*\}) less to cast if you "
    r"control an artifact and (?P<second>\{[1-9][0-9]*\}) less to cast if "
    r"you control an enchantment\.?$",
    re.IGNORECASE,
)
_COUNT_WORDS = {
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
_COUNT_PATTERN = r"(?:[1-9][0-9]*|one|two|three|four|five|six|seven|eight|nine|ten)"


def _count_value(value: str) -> int:
    return int(value) if value.isdigit() else _COUNT_WORDS[value.casefold()]


def _reduction_pairs(token: str) -> tuple[tuple[str, int], ...] | None:
    match = re.fullmatch(r"\{(?P<value>[1-9][0-9]*|[WUBRGC])\}", token)
    if match is None:
        return None
    value = match.group("value")
    return (("GENERIC", int(value)),) if value.isdigit() else ((value, 1),)


def _singular_quality(value: str) -> str:
    normalized = " ".join(value.casefold().split())
    irregular = {"elves": "elf", "dwarves": "dwarf"}
    if normalized in irregular:
        return irregular[normalized]
    if normalized.endswith("ies"):
        return normalized[:-3] + "y"
    if normalized.endswith("s") and not normalized.endswith("ss"):
        return normalized[:-1]
    return normalized


def _quality_query(value: str, *, zone: str) -> ObjectQuerySpec | None:
    normalized = " ".join(value.casefold().split())
    normalized = re.sub(r"^(?:a|an) ", "", normalized)
    normalized = re.sub(r" cards?$", "", normalized)
    fields: dict[str, Any] = {"zones": (zone,)}
    if normalized in {"permanent", "permanents"}:
        pass
    elif normalized in {"card", "cards"}:
        pass
    elif normalized in {"nonland permanent", "nonland permanents"}:
        fields["excluded_types"] = ("land",)
    elif normalized in {"creature", "creatures"}:
        fields["types_all"] = ("creature",)
    elif normalized in {"land", "lands"}:
        fields["types_all"] = ("land",)
    elif normalized in {"artifact", "artifacts"}:
        fields["types_all"] = ("artifact",)
    elif normalized in {"enchantment", "enchantments"}:
        fields["types_all"] = ("enchantment",)
    elif normalized in {"artifact creature", "artifact creatures"}:
        fields["types_all"] = ("artifact", "creature")
    elif normalized in {
        "instant and sorcery",
        "instant or sorcery",
        "instant and/or sorcery",
    }:
        fields["types_any"] = ("instant", "sorcery")
    elif normalized in {"artifact and/or creature", "artifact or creature"}:
        fields["types_any"] = ("artifact", "creature")
    elif normalized in {"noncreature artifact", "noncreature artifacts"}:
        fields["types_all"] = ("artifact",)
        fields["excluded_types"] = ("creature",)
    elif normalized in {"noncreature enchantment", "noncreature enchantments"}:
        fields["types_all"] = ("enchantment",)
        fields["excluded_types"] = ("creature",)
    elif normalized in {"legendary creature", "legendary creatures"}:
        fields["types_all"] = ("creature",)
        fields["supertypes_all"] = ("legendary",)
    elif normalized in {"basic land", "basic lands"}:
        fields["types_all"] = ("land",)
        fields["supertypes_all"] = ("basic",)
    elif normalized == "green permanent":
        fields["colors_all"] = ("G",)
    elif normalized in {"green creature", "green creatures"}:
        fields["types_all"] = ("creature",)
        fields["colors_all"] = ("G",)
    elif normalized == "multicolored permanent":
        fields["minimum_color_count"] = 2
    elif normalized == "creature with flying":
        fields["types_all"] = ("creature",)
        fields["keywords_all"] = ("flying",)
    elif normalized == "creature with a +1/+1 counter on it":
        fields["types_all"] = ("creature",)
        fields["state_predicate"] = PermanentStatePredicateSpec(
            counter_name="+1/+1",
            minimum_counter_count=1,
        )
    elif normalized == "permanent with oil counters on it":
        fields["state_predicate"] = PermanentStatePredicateSpec(
            counter_name="oil",
            minimum_counter_count=1,
        )
    elif normalized in {"human creature", "human creatures"}:
        fields["types_all"] = ("creature",)
        fields["subtypes_all"] = ("human",)
    elif normalized in {"non-human creature", "non-human creatures"}:
        fields["types_all"] = ("creature",)
        fields["excluded_subtypes"] = ("human",)
    else:
        subtype = _singular_quality(normalized)
        subtype = (
            subtype
            if subtype in _NONCREATURE_SUBTYPES
            else canonical_creature_subtype(subtype)
        )
        if subtype is None:
            return None
        fields["subtypes_all"] = (subtype,)
    try:
        return ObjectQuerySpec(**fields)
    except ObjectQueryError:
        return None


def _relative_query(
    scope: CastReductionQueryScope,
    query: ObjectQuerySpec | None,
) -> CastReductionObjectQuery | None:
    if query is None:
        return None
    return CastReductionObjectQuery(scope=scope, query=query)


def _term(
    reduction: tuple[tuple[str, int], ...],
    metric: CastReductionMetric,
) -> SelfSpellCostReductionTerm:
    return SelfSpellCostReductionTerm(reduction=reduction, metric=metric)


def _public_condition_metric(condition: str) -> CastReductionMetric | None:
    normalized = " ".join(condition.casefold().split())
    normalized = normalized.removeprefix("if ")
    patterns = (
        (
            rf"you control (?P<count>{_COUNT_PATTERN}) or more (?P<quality>.+)",
            CastReductionQueryScope.CONTROLLER_ZONE,
            None,
        ),
        (
            r"you control (?P<quality>.+)",
            CastReductionQueryScope.CONTROLLER_ZONE,
            1,
        ),
        (
            r"an opponent controls (?P<quality>.+)",
            CastReductionQueryScope.OPPONENT_ZONES,
            1,
        ),
        (
            rf"your opponents control (?P<count>{_COUNT_PATTERN}) or more (?P<quality>.+)",
            CastReductionQueryScope.OPPONENT_ZONES,
            None,
        ),
        (
            rf"there are (?P<count>{_COUNT_PATTERN}) or more (?P<quality>.+) on the battlefield",
            CastReductionQueryScope.ALL_ZONES,
            None,
        ),
        (
            rf"an opponent has (?P<count>{_COUNT_PATTERN}) or more (?P<quality>.+) in their graveyard",
            CastReductionQueryScope.OPPONENT_ZONES,
            None,
        ),
    )
    if normalized == "an opponent controls no basic lands":
        query = _relative_query(
            CastReductionQueryScope.OPPONENT_ZONES,
            _quality_query("basic lands", zone="battlefield"),
        )
        return (
            CastReductionMetric(
                kind=CastReductionMetricKind.FIXED_PUBLIC_THRESHOLD,
                queries=(query,),
                maximum=0,
            )
            if query is not None
            else None
        )
    if normalized == "you control a human creature and a non-human creature":
        queries = tuple(
            value
            for value in (
                _relative_query(
                    CastReductionQueryScope.CONTROLLER_ZONE,
                    _quality_query(quality, zone="battlefield"),
                )
                for quality in ("human creature", "non-human creature")
            )
            if value is not None
        )
        return (
            CastReductionMetric(
                kind=CastReductionMetricKind.FIXED_PUBLIC_THRESHOLD,
                queries=queries,
                minimum=1,
                require_all=True,
            )
            if len(queries) == 2
            else None
        )
    for pattern, scope, default_count in patterns:
        match = re.fullmatch(pattern, normalized)
        if match is None:
            continue
        raw_count = match.groupdict().get("count")
        count = _count_value(raw_count) if raw_count else int(default_count or 0)
        quality = match.group("quality")
        zone = "graveyard" if "graveyard" in pattern else "battlefield"
        query = _relative_query(scope, _quality_query(quality, zone=zone))
        if query is None:
            return None
        return CastReductionMetric(
            kind=CastReductionMetricKind.FIXED_PUBLIC_THRESHOLD,
            queries=(query,),
            minimum=count,
        )
    return None


def _turn_fact_metric(condition: str) -> CastReductionMetric | None:
    normalized = " ".join(condition.casefold().split())
    fact = {
        "if a creature died this turn": CastReductionTurnFact.CREATURE_DIED,
        "if you've cast another spell this turn": (
            CastReductionTurnFact.CONTROLLER_CAST_ANOTHER_SPELL
        ),
        "if an opponent cast two or more spells this turn": (
            CastReductionTurnFact.OPPONENT_CAST_TWO_SPELLS
        ),
        "during your turn": CastReductionTurnFact.CONTROLLER_TURN,
    }.get(normalized)
    return (
        CastReductionMetric(
            kind=CastReductionMetricKind.TURN_FACT,
            turn_fact=fact,
        )
        if fact is not None
        else None
    )


def _object_count_metric(value: str) -> CastReductionMetric | None:
    normalized = " ".join(value.casefold().split())
    queries: list[CastReductionObjectQuery] = []
    if normalized == "cave you control and each cave card in your graveyard":
        parts = (
            (CastReductionQueryScope.CONTROLLER_ZONE, "Cave", "battlefield"),
            (CastReductionQueryScope.CONTROLLER_ZONE, "Cave card", "graveyard"),
        )
    elif normalized == "permanent you control with oil counters on it":
        parts = ((CastReductionQueryScope.CONTROLLER_ZONE, "permanent with oil counters on it", "battlefield"),)
    elif match := re.fullmatch(
        r"(?P<quality>.+?) you own in exile and in your graveyard",
        normalized,
    ):
        parts = tuple(
            (CastReductionQueryScope.CONTROLLER_ZONE, match.group("quality"), zone)
            for zone in ("exile", "graveyard")
        )
    elif match := re.fullmatch(r"(?P<quality>.+?) in your graveyard", normalized):
        parts = ((CastReductionQueryScope.CONTROLLER_ZONE, match.group("quality"), "graveyard"),)
    elif match := re.fullmatch(r"(?P<quality>.+?) on the battlefield", normalized):
        parts = ((CastReductionQueryScope.ALL_ZONES, match.group("quality"), "battlefield"),)
    elif match := re.fullmatch(r"(?P<quality>.+?) your opponents control", normalized):
        parts = ((CastReductionQueryScope.OPPONENT_ZONES, match.group("quality"), "battlefield"),)
    elif match := re.fullmatch(r"(?P<quality>.+?) you control", normalized):
        parts = ((CastReductionQueryScope.CONTROLLER_ZONE, match.group("quality"), "battlefield"),)
    else:
        return None
    for scope, quality, zone in parts:
        query = _relative_query(scope, _quality_query(quality, zone=zone))
        if query is None:
            return None
        queries.append(query)
    return CastReductionMetric(
        kind=CastReductionMetricKind.OBJECT_COUNT,
        queries=tuple(queries),
    )


def self_spell_cost_reduction_handler(
    text: str,
) -> CastCostModifierTemplate | None:
    """Lower one source-pinned reduction for the spell carrying this line."""

    normalized = " ".join(text.strip().split())
    double = _DOUBLE_PUBLIC_REDUCTION.fullmatch(normalized)
    if double is not None:
        terms: list[SelfSpellCostReductionTerm] = []
        for token, quality in (
            (double.group("first"), "artifact"),
            (double.group("second"), "enchantment"),
        ):
            reduction = _reduction_pairs(token)
            metric = _public_condition_metric(f"you control an {quality}")
            if reduction is None or metric is None:
                return None
            terms.append(_term(reduction, metric))
        specification = SelfSpellCostReductionSpec(tuple(terms))
    elif domain := _DOMAIN_REDUCTION.fullmatch(normalized):
        reduction = _reduction_pairs(domain.group("amount"))
        if reduction is None:
            return None
        specification = SelfSpellCostReductionSpec(
            (_term(reduction, CastReductionMetric(kind=CastReductionMetricKind.DOMAIN)),)
        )
    elif variable := _SELF_VARIABLE_REDUCTION.fullmatch(normalized):
        metric_text = variable.group("metric")
        devotion = re.fullmatch(
            r"your devotion to (?P<color>white|blue|black|red|green)(?:\. \(.+\))?",
            metric_text,
            re.IGNORECASE,
        )
        total = re.fullmatch(
            r"the total mana value of (?P<quality>.+?) you control",
            metric_text,
            re.IGNORECASE,
        )
        if devotion is not None:
            color = _COLORS[devotion.group("color").casefold()]
            metric = CastReductionMetric(
                kind=CastReductionMetricKind.DEVOTION,
                color=color,
            )
        elif total is not None:
            query = _relative_query(
                CastReductionQueryScope.CONTROLLER_ZONE,
                _quality_query(total.group("quality"), zone="battlefield"),
            )
            if query is None:
                return None
            metric = CastReductionMetric(
                kind=CastReductionMetricKind.TOTAL_MANA_VALUE,
                queries=(query,),
            )
        else:
            return None
        specification = SelfSpellCostReductionSpec(
            (_term((("GENERIC", 1),), metric),)
        )
    else:
        match = _SELF_REDUCTION.fullmatch(normalized)
        if match is None:
            return None
        reduction = _reduction_pairs(match.group("amount"))
        if reduction is None:
            return None
        metric_text = match.group("metric")
        if metric_text.casefold().startswith("for each "):
            metric = _object_count_metric(metric_text[9:])
        else:
            metric = _public_condition_metric(metric_text) or _turn_fact_metric(
                metric_text
            )
        if metric is None:
            return None
        specification = SelfSpellCostReductionSpec((_term(reduction, metric),))
    return (
        "self-spell-cost-public-reduction-v1",
        {
            "handler_id": SELF_SPELL_COST_REDUCTION_HANDLER_ID,
            "schema_version": 1,
            "event": FIXED_SPELL_COST_REDUCTION_EVENT,
            "reduction": specification.to_dict(),
        },
        SELF_SPELL_COST_REDUCTION_CAPABILITY_ID,
    )


def _single_spell_quality(
    text: str,
) -> tuple[str, dict[str, Any]] | None:
    words = text.casefold().split()
    if len(words) == 1:
        word = words[0]
        if word in _CARD_TYPES:
            return "type", {"types_all": (word,)}
        if word in _COLORS:
            return "color", {"colors_all": (_COLORS[word],)}
        if word == "colorless":
            return "colorless", {"colorless": True}
        if word in _SUPERTYPES:
            return "supertype", {"supertypes_all": (word,)}
        if word == "noncreature":
            return "excluded_type", {"excluded_types": ("creature",)}
        subtype = (
            word
            if word in _NONCREATURE_SUBTYPES
            else canonical_creature_subtype(word)
        )
        if subtype is not None:
            return "subtype", {"subtypes_all": (subtype,)}
        return None
    if len(words) != 2:
        return None
    qualifier, subject = words
    if subject in _CARD_TYPES:
        if qualifier in _COLORS:
            return "conjunction", {
                "types_all": (subject,),
                "colors_all": (_COLORS[qualifier],),
            }
        if qualifier == "colorless":
            return "conjunction", {
                "types_all": (subject,),
                "colorless": True,
            }
        if qualifier in _SUPERTYPES:
            return "conjunction", {
                "types_all": (subject,),
                "supertypes_all": (qualifier,),
            }
        return None
    subtype = canonical_creature_subtype(subject)
    if subtype is None or qualifier not in {*_COLORS, "colorless"}:
        return None
    fields: dict[str, Any] = {"subtypes_all": (subtype,)}
    if qualifier == "colorless":
        fields["colorless"] = True
    else:
        fields["colors_all"] = (_COLORS[qualifier],)
    return "conjunction", fields


def _fixed_spell_predicate(subject: str) -> ObjectQuerySpec | None:
    normalized = subject.strip()
    if normalized.casefold() == "spells you cast":
        return ObjectQuerySpec()
    suffix = " spells you cast"
    if not normalized.casefold().endswith(suffix):
        return None
    qualities = normalized[: -len(suffix)].strip()
    # Oracle repeats "spells" in lists such as "White spells and black
    # spells". It is only a grammatical carrier inside this closed subject.
    qualities = re.sub(r"\bspells\b", "", qualities, flags=re.IGNORECASE)
    parts = tuple(
        part.strip()
        for part in re.split(
            r"\s*(?:,|\band\b)\s*",
            qualities,
            flags=re.IGNORECASE,
        )
        if part.strip()
    )
    parsed = tuple(_single_spell_quality(part) for part in parts)
    if not parsed or any(value is None for value in parsed):
        return None
    typed = tuple(value for value in parsed if value is not None)
    if len(typed) == 1:
        return ObjectQuerySpec(**typed[0][1])
    kinds = {kind for kind, _fields in typed}
    if len(kinds) != 1:
        return None
    kind = next(iter(kinds))
    values = tuple(
        next(iter(fields.values()))[0]
        for _kind, fields in typed
    )
    if kind == "type":
        return ObjectQuerySpec(types_any=values)
    if kind == "color":
        return ObjectQuerySpec(colors_any=values)
    if kind == "subtype":
        return ObjectQuerySpec(subtypes_any=values)
    return None


def static_fixed_spell_cost_reduction_handler(
    text: str,
) -> CastCostModifierTemplate | None:
    """Lower one unconditional generic reduction over a fixed spell set."""

    match = _FIXED_GENERIC_SPELL_REDUCTION.fullmatch(text.strip())
    if match is None:
        return None
    try:
        predicate = _fixed_spell_predicate(match.group("subject"))
    except ObjectQueryError:
        return None
    if predicate is None:
        return None
    return (
        "fixed-query-spell-cost-reduction-v1",
        {
            "handler_id": FIXED_SPELL_COST_REDUCTION_HANDLER_ID,
            "schema_version": 1,
            "event": FIXED_SPELL_COST_REDUCTION_EVENT,
            "affected_controller": "source_controller",
            "predicate": predicate.to_dict(),
            "generic_reduction": int(match.group("amount")),
        },
        FIXED_SPELL_COST_REDUCTION_CAPABILITY_ID,
    )


__all__ = [
    "CastCostModifierTemplate",
    "self_spell_cost_reduction_handler",
    "static_fixed_spell_cost_reduction_handler",
]
