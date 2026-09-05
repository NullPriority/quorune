from __future__ import annotations

"""Fixed resolution-locked public creature-set characteristic grammar."""

import re
from typing import Any, Mapping

from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from .continuous_templates import controlled_characteristic_until_end_of_turn_effect
from .fixed_resolution_characteristic_queries import (
    fixed_resolution_characteristic_query_is_closed,
)


FIXED_PUBLIC_CHARACTERISTIC_SET_TEMPLATE_ID = (
    "modify-public-creature-set-fixed-characteristics-eot-v1"
)
_TRAILING_REMINDER = re.compile(r"\s*\([^()]*(?:\([^()]*\)[^()]*)*\)\s*$")
_PUBLIC_CREATURE_SET = re.compile(
    r"^(?P<subject>All creatures|Creatures your opponents control|"
    r"Creatures target player controls|Attacking creatures(?: you control)?|"
    r"Blocking creatures(?: you control)?|Other attacking creatures|"
    r"Each other attacking creature) "
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


def _public_query(subject: str) -> tuple[ObjectQuerySpec, Mapping[str, Any] | None]:
    normalized = " ".join(subject.casefold().split())
    fields: dict[str, Any] = {
        "zones": ("battlefield",),
        "types_all": ("creature",),
    }
    target_schema: Mapping[str, Any] | None = None
    if normalized == "creatures your opponents control":
        fields["excluded_controllers"] = ("$controller",)
    elif normalized == "creatures target player controls":
        fields["controller"] = "$target.0"
        target_schema = dict(_PLAYER_TARGET_SCHEMA)
    elif normalized in {
        "attacking creatures you control",
        "blocking creatures you control",
    }:
        fields["controller"] = "$controller"
    if "attacking" in normalized:
        fields["state_predicate"] = PermanentStatePredicateSpec(attacking=True)
    elif "blocking" in normalized:
        fields["state_predicate"] = PermanentStatePredicateSpec(blocking=True)
    if normalized in {
        "other attacking creatures",
        "each other attacking creature",
    }:
        fields["exclude_ref"] = "$source"
    query = ObjectQuerySpec(**fields)
    if not fixed_resolution_characteristic_query_is_closed(
        query,
        target_schema=target_schema,
    ):
        raise ValueError("Public characteristic-set query is not closed")
    return query, target_schema


def fixed_public_characteristic_set_effect_template(
    oracle_line: str,
) -> tuple[
    str,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
] | None:
    """Lower one fixed public creature set and lock it at resolution."""

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
    query, target_schema = _public_query(match.group("subject"))
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
