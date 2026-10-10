from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .characteristic_evaluation import type_parts
from .defender_permission import DefenderAttackPermission
from .ability_fragments import canonical_ability_fragments
from .keyword_abilities import (
    EffectiveKeywordError,
    normalized_characteristic_keywords,
)


DEFENDER_KEYWORD = "defender"


class DefenderRuleError(ValueError):
    """The current characteristic snapshot is malformed."""


def defender_prohibits_attack(data: Mapping[str, Any]) -> bool:
    """Whether the current represented creature has Defender.

    The declaration coordinator owns every other attacker restriction. This
    query consumes only the current effective type and keyword snapshot, so
    advertised candidates and accepted declarations can share one verdict.
    """

    if not isinstance(data, Mapping):
        raise DefenderRuleError("Effective characteristics must be a mapping")
    type_line = data.get("type_line", "")
    if not isinstance(type_line, str):
        raise DefenderRuleError("Effective type line must be a string")
    if "creature" not in type_parts(type_line)[0]:
        return False
    try:
        keywords = normalized_characteristic_keywords(data)
    except EffectiveKeywordError as exc:
        raise DefenderRuleError(str(exc)) from exc
    fragments = canonical_ability_fragments(data.get('ability_fragments', ()))
    permitted = any(isinstance(fragment, DefenderAttackPermission) for fragment in fragments)
    return DEFENDER_KEYWORD in keywords and not permitted


__all__ = [
    "DEFENDER_KEYWORD",
    "DefenderRuleError",
    "defender_prohibits_attack",
]
