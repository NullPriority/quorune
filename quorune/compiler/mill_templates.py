from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


MILL_MECHANIC_ID = "mill"
MILL_CAPABILITY_ID = "zone.mill.fixed"
_LARGE_NUMBER_WORDS = {
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}
_MILL_COUNT_PATTERN = (
    rf"{FIXED_COUNT_PATTERN}|" + "|".join(_LARGE_NUMBER_WORDS)
)
_FIXED_MILL = re.compile(
    rf"^(?:(?P<subject>you|target player|target opponent|each player|each opponent) )?"
    rf"(?P<verb>mill|mills) (?P<count>{_MILL_COUNT_PATTERN}) "
    rf"(?P<card_word>cards?)\.?$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FixedMillEffectTemplate:
    template_id: str
    effect: Mapping[str, Any]
    target_schema: Mapping[str, Any] | None = None
    mechanic_ids: tuple[str, ...] = (MILL_MECHANIC_ID,)

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            (self.effect,),
            self.target_schema,
            self.mechanic_ids,
        )


def _mill_count(value: str) -> int:
    normalized = value.casefold()
    return (
        _LARGE_NUMBER_WORDS[normalized]
        if normalized in _LARGE_NUMBER_WORDS
        else fixed_number(normalized)
    )


def _target_schema(relation: str) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "zones": ["player"],
        "categories": ["player"],
        "count": 1,
    }
    if relation == "opponent":
        schema["player_relation"] = "opponent"
    return schema


def fixed_mill_effect_template(text: str) -> FixedMillEffectTemplate | None:
    """Lower one mandatory fixed-count controller or direct-player Mill."""

    match = _FIXED_MILL.fullmatch(text.strip())
    if match is None:
        return None
    subject = str(match.group("subject") or "controller").casefold()
    verb = str(match.group("verb")).casefold()
    if (subject in {"controller", "you"}) != (verb == "mill"):
        return None
    if (subject.startswith("target ") or subject in {"each player","each opponent"}) != (verb == "mills"):
        return None
    count = _mill_count(match.group("count"))
    if (count == 1) != (match.group("card_word").casefold() == "card"):
        return None
    if subject in {"each player","each opponent"}:
        return FixedMillEffectTemplate(
            template_id="mill-fixed-each-player-v1" if subject=='each player' else 'mill-fixed-each-opponent-v1',
            effect={"op": "mill", "scope": "all_active_players" if subject=='each player' else 'controller_opponents', "count": count},
        )
    player = "$controller"
    target_schema = None
    mechanic_ids = (MILL_MECHANIC_ID,)
    template_subject = "controller"
    if subject.startswith("target "):
        relation = "opponent" if subject.endswith("opponent") else "any"
        player = "$target.0"
        target_schema = _target_schema(relation)
        mechanic_ids = (MILL_MECHANIC_ID, "cr-115-targets")
        template_subject = f"target-{relation}"
    return FixedMillEffectTemplate(
        template_id=f"mill-fixed-{template_subject}-v1",
        effect={
            "op": "mill",
            "player": player,
            "count": count,
        },
        target_schema=target_schema,
        mechanic_ids=mechanic_ids,
    )


__all__ = [
    "fixed_mill_effect_template",
    "FixedMillEffectTemplate",
    "MILL_CAPABILITY_ID",
    "MILL_MECHANIC_ID",
]
