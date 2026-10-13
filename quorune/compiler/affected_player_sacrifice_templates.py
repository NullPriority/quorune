from __future__ import annotations

"""Closed affected-player choices for fixed permanent sacrifices."""

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import hashlib
import re
from typing import Any, Mapping

from ..object_predicate import ObjectQuerySpec
from .creature_subtypes import canonical_creature_subtype_surface
from ..util import stable_json


FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC = (
    "fixed-affected-player-sacrifice"
)
FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY = (
    "choice.affected_player.fixed_sacrifice"
)
_SACRIFICE_MECHANIC = "sacrifice"
_TARGETS_MECHANIC = "cr-115-targets"
_INSTRUCTION = re.compile(
    r"^(?:(?P<subject>target player|target opponent|each player|each opponent|you) )?"
    r"(?P<verb>sacrifices?) (?P<count>a|an|one|two) (?P<object>.+?)\.?$",
    re.IGNORECASE,
)


class AffectedPlayerSacrificeSubject(str, Enum):
    CONTROLLER = "controller"
    TARGET_PLAYER = "target_player"
    TARGET_OPPONENT = "target_opponent"
    EACH_PLAYER = "each_player"
    EACH_OPPONENT = "each_opponent"

    @property
    def targeted(self) -> bool:
        return self in {
            AffectedPlayerSacrificeSubject.TARGET_PLAYER,
            AffectedPlayerSacrificeSubject.TARGET_OPPONENT,
        }

    @property
    def opponent_only(self) -> bool:
        return self in {
            AffectedPlayerSacrificeSubject.TARGET_OPPONENT,
            AffectedPlayerSacrificeSubject.EACH_OPPONENT,
        }


_SUBJECTS = {
    "you": AffectedPlayerSacrificeSubject.CONTROLLER,
    "target player": AffectedPlayerSacrificeSubject.TARGET_PLAYER,
    "target opponent": AffectedPlayerSacrificeSubject.TARGET_OPPONENT,
    "each player": AffectedPlayerSacrificeSubject.EACH_PLAYER,
    "each opponent": AffectedPlayerSacrificeSubject.EACH_OPPONENT,
}


def _predicate(phrase: str, *, count: int) -> ObjectQuerySpec | None:
    normalized = " ".join(phrase.casefold().split())
    if normalized.endswith(" of their choice with flying"):
        normalized = normalized.removesuffix(
            " of their choice with flying"
        ) + " with flying"
    elif normalized.endswith(" of their choice"):
        normalized = normalized.removesuffix(" of their choice")
    if count == 2:
        normalized = {
            "artifacts": "artifact",
            "creatures": "creature",
            "enchantments": "enchantment",
            "lands": "land",
            "permanents": "permanent",
            "planeswalkers": "planeswalker",
        }.get(normalized, normalized if canonical_creature_subtype_surface(normalized) is not None else "")
    if not normalized:
        return None
    if normalized == "permanent":
        return ObjectQuerySpec(zones=("battlefield",))
    if normalized in {
        "artifact",
        "creature",
        "enchantment",
        "land",
        "planeswalker",
    }:
        return ObjectQuerySpec(
            zones=("battlefield",),
            types_all=(normalized,),
        )
    if normalized in {
        "artifact or enchantment",
        "creature or enchantment",
        "creature or planeswalker",
    }:
        return ObjectQuerySpec(
            zones=("battlefield",),
            types_any=tuple(normalized.split(" or ")),
        )
    if normalized == "creature token":
        return ObjectQuerySpec(
            zones=("battlefield",),
            types_all=("creature",),
            token=True,
        )
    if normalized in {"nontoken artifact", "nontoken creature"}:
        return ObjectQuerySpec(
            zones=("battlefield",),
            types_all=(normalized.removeprefix("nontoken "),),
            token=False,
        )
    if normalized == "nonartifact creature":
        return ObjectQuerySpec(
            zones=("battlefield",),
            types_all=("creature",),
            excluded_types=("artifact",),
        )
    if normalized == "creature with flying":
        return ObjectQuerySpec(
            zones=("battlefield",),
            types_all=("creature",),
            keywords_all=("flying",),
        )
    subtype = canonical_creature_subtype_surface(normalized)
    if subtype is not None:
        return ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), subtypes_all=(subtype,))
    return None


_CLOSED_PREDICATES = frozenset(
    stable_json(value.to_dict())
    for phrase in (
        "artifact",
        "creature",
        "enchantment",
        "land",
        "permanent",
        "planeswalker",
        "artifact or enchantment",
        "creature or enchantment",
        "creature or planeswalker",
        "creature token",
        "nontoken artifact",
        "nontoken creature",
        "nonartifact creature",
        "creature with flying",
    )
    for value in (_predicate(phrase, count=1),)
    if value is not None
)


def fixed_affected_player_sacrifice_predicate_is_closed(
    value: Mapping[str, Any],
) -> bool:
    try:
        predicate = ObjectQuerySpec.from_dict(value)
    except (TypeError, ValueError):
        return False
    if stable_json(predicate.to_dict()) in _CLOSED_PREDICATES:
        return True
    if len(predicate.subtypes_all) != 1:
        return False
    subtype = canonical_creature_subtype_surface(predicate.subtypes_all[0])
    return subtype is not None and predicate == ObjectQuerySpec(
        zones=("battlefield",), types_all=("creature",), subtypes_all=(subtype,))


@dataclass(frozen=True, slots=True)
class FixedAffectedPlayerSacrificeTemplate:
    subject: AffectedPlayerSacrificeSubject
    count: int
    predicate: ObjectQuerySpec

    def __post_init__(self) -> None:
        if not isinstance(self.subject, AffectedPlayerSacrificeSubject):
            raise ValueError("Affected-player sacrifice subject is malformed")
        if type(self.count) is not int or self.count not in {1, 2}:
            raise ValueError("Affected-player sacrifice count is unsupported")
        if not fixed_affected_player_sacrifice_predicate_is_closed(
            self.predicate.to_dict()
        ):
            raise ValueError("Affected-player sacrifice predicate is unsupported")

    @property
    def template_id(self) -> str:
        digest = hashlib.sha256(
            stable_json(self.predicate.to_dict()).encode("utf-8")
        ).hexdigest()[:12]
        return (
            f"fixed-affected-player-sacrifice-{self.subject.value}-"
            f"{self.count}-{digest}-v1"
        )

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        players: str | list[str] = {
            AffectedPlayerSacrificeSubject.CONTROLLER: ["$controller"],
            AffectedPlayerSacrificeSubject.EACH_PLAYER: "all",
            AffectedPlayerSacrificeSubject.EACH_OPPONENT: "opponents",
        }.get(self.subject, ["$target.0"])
        effect: dict[str, Any] = {
            "op": "choose_cards_apnap",
            "actor": "$controller",
            "players": players,
            "zone": "battlefield",
            "predicate": self.predicate.to_dict(),
            "count": self.count,
            "then": "sacrifice",
            "prompt": "Choose the required permanent(s) to sacrifice.",
        }
        if self.subject.targeted:
            effect["target"] = "$target.0"
        return (deepcopy(effect),)

    @property
    def target_schema(self) -> Mapping[str, Any] | None:
        if not self.subject.targeted:
            return None
        return {
            "zones": ["player"],
            "categories": ["player"],
            "player_relation": (
                "opponent" if self.subject.opponent_only else "any"
            ),
            "count": 1,
        }

    @property
    def mechanics(self) -> tuple[str, ...]:
        return (
            FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC,
            _SACRIFICE_MECHANIC,
            *((_TARGETS_MECHANIC,) if self.subject.targeted else ()),
        )

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
            self.effects,
            self.target_schema,
            self.mechanics,
        )


def fixed_affected_player_sacrifice_effect_template(
    text: str,
) -> FixedAffectedPlayerSacrificeTemplate | None:
    """Lower one mandatory fixed affected-player sacrifice instruction."""

    normalized = " ".join(text.strip().split())
    match = _INSTRUCTION.fullmatch(normalized)
    if match is None:
        return None
    subject = (match.group("subject") or "you").casefold()
    if (subject == "you") != (match.group("verb").casefold() == "sacrifice"):
        return None
    count_word = match.group("count").casefold()
    count = 2 if count_word == "two" else 1
    predicate = _predicate(match.group("object"), count=count)
    if predicate is None:
        return None
    return FixedAffectedPlayerSacrificeTemplate(
        subject=_SUBJECTS[subject],
        count=count,
        predicate=predicate,
    )


__all__ = [
    "AffectedPlayerSacrificeSubject",
    "FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY",
    "FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC",
    "FixedAffectedPlayerSacrificeTemplate",
    "fixed_affected_player_sacrifice_effect_template",
    "fixed_affected_player_sacrifice_predicate_is_closed",
]
