from __future__ import annotations

"""Fixed resolution-locked public characteristic-set grammar."""

from dataclasses import replace
import re
from typing import Any, Mapping

from ..object_predicate import ObjectQuerySpec
from ..target_numeric import TargetNumericCharacteristic, TargetNumericCharacteristicSpec, TargetNumericComparison
from .continuous_templates import controlled_characteristic_until_end_of_turn_effect
from .fixed_resolution_characteristic_queries import (
    fixed_resolution_characteristic_query_is_closed,
)
from .public_state_queries import (
    fixed_characteristic_battlefield_query_subject,
)


FIXED_PUBLIC_CHARACTERISTIC_SET_TEMPLATE_ID = (
    "modify-public-creature-set-fixed-characteristics-eot-v1"
)
_TRAILING_REMINDER = re.compile(r"\s*\([^()]*(?:\([^()]*\)[^()]*)*\)\s*$")
_PUBLIC_CREATURE_SET = re.compile(
    r"^(?P<subject>.+?) "
    r"(?P<verb>get|gets|gain|gains) (?P<result>.+)$",
    re.IGNORECASE,
)
_PLAYER_TARGET_SCHEMA = {
    "zones": ["player"],
    "categories": ["player"],
    "player_relation": "any",
    "count": 1,
}


def _until_end_of_turn_body(text: str) -> str | None:
    normalized = _TRAILING_REMINDER.sub("", text.strip()).strip()
    prefix = re.fullmatch(
        r"Until end of turn, (?P<body>.+?)\.?",
        normalized,
        re.IGNORECASE,
    )
    suffix = re.fullmatch(
        r"(?P<body>.+?) until end of turn\.?",
        normalized,
        re.IGNORECASE,
    )
    match = prefix or suffix
    return match.group("body").rstrip(".") if match is not None else None


def _public_query(
    subject: str,
) -> tuple[ObjectQuerySpec, Mapping[str, Any] | None] | None:
    normalized = " ".join(subject.casefold().split())
    if normalized == "creatures target player controls":
        return (
            ObjectQuerySpec(
                zones=("battlefield",),
                controller="$target.0",
                types_all=("creature",),
            ),
            dict(_PLAYER_TARGET_SCHEMA),
        )
    numeric = re.fullmatch(r'(?P<subject>.+?) with (?P<stat>power|toughness) (?P<amount>[0-9]+) or (?P<comparison>greater|less)', subject, re.IGNORECASE)
    parsed = fixed_characteristic_battlefield_query_subject(numeric['subject'] if numeric else subject)
    if parsed is None:
        return None
    relation, predicate, exclude_source = parsed
    if numeric:
        if 'creature' not in predicate.types_all:
            return None
        predicate = replace(predicate, numeric_characteristic=TargetNumericCharacteristicSpec(
            TargetNumericCharacteristic(numeric['stat'].casefold()),
            TargetNumericComparison.AT_LEAST if numeric['comparison'].casefold() == 'greater' else TargetNumericComparison.AT_MOST,
            int(numeric['amount'])))
    if not (
        "creature" in predicate.types_all
        or (not predicate.types_all and (predicate.subtypes_all or predicate.subtypes_any))
    ):
        return None
    query = predicate
    if relation == "source_controller":
        query = replace(query, controller="$controller")
    elif relation == "source_opponents":
        query = replace(
            query,
            excluded_controllers=("$controller",),
        )
    elif relation != "any":
        return None
    if exclude_source:
        query = replace(query, exclude_ref="$source")
    if not fixed_resolution_characteristic_query_is_closed(
        query,
        target_schema=None,
    ):
        return None
    return query, None


def _keyword_before_stat_grant(oracle_line: str) -> str:
    """Reorder one whole result before the existing closed keyword validator."""
    body = _until_end_of_turn_body(oracle_line)
    match = _PUBLIC_CREATURE_SET.fullmatch(body or '')
    if match is None or match['verb'].casefold() not in {'gain', 'gains'}:
        return oracle_line
    parts = re.fullmatch(
        r'(?P<keywords>.+?) and gets? (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+)',
        match['result'], re.I,
    )
    if parts is None:
        return oracle_line
    return (match['subject']+' get '+parts['power']+'/'+parts['toughness']+' and gain '
            +parts['keywords']+' until end of turn.')


def fixed_public_characteristic_set_effect_template(
    oracle_line: str,
) -> tuple[
    str,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
] | None:
    """Lower one closed creature/subtype set and lock it at resolution."""

    oracle_line = _keyword_before_stat_grant(oracle_line)
    controlled = controlled_characteristic_until_end_of_turn_effect(oracle_line)
    if controlled is not None:
        template_id, effects, mechanics = controlled
        return template_id, effects, None, mechanics
    body = _until_end_of_turn_body(oracle_line)
    match = _PUBLIC_CREATURE_SET.fullmatch(body or "")
    if match is None:
        return None
    verb = (
        "get"
        if match.group("verb").casefold() in {"get", "gets"}
        else "gain"
    )
    represented = controlled_characteristic_until_end_of_turn_effect(
        f"Creatures you control {verb} {match.group('result')} until end of turn."
    )
    if represented is None:
        return None
    _template_id, effects, mechanics = represented
    query_result = _public_query(match.group("subject"))
    if query_result is None:
        return None
    query, target_schema = query_result
    effect = {
        **dict(effects[0]),
        "predicate": query.to_dict(),
    }
    return (
        FIXED_PUBLIC_CHARACTERISTIC_SET_TEMPLATE_ID,
        (effect,),
        target_schema,
        tuple(
            sorted(
                {
                    *mechanics,
                    *({"cr-115-targets"} if target_schema is not None else set()),
                }
            )
        ),
    )


__all__ = [
    "FIXED_PUBLIC_CHARACTERISTIC_SET_TEMPLATE_ID",
    "fixed_public_characteristic_set_effect_template",
]
