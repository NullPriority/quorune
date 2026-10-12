from __future__ import annotations

"""Closed affected-player choices for fixed card discards."""

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping

from ..object_predicate import ObjectQuerySpec
from ..util import stable_json
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


FIXED_AFFECTED_PLAYER_DISCARD_MECHANIC = "fixed-affected-player-discard"
FIXED_AFFECTED_PLAYER_DISCARD_CAPABILITY = "choice.affected_player.fixed_discard"
FIXED_CONTROLLER_DISCARD_MECHANIC = "fixed-controller-card-discard"
FIXED_CONTROLLER_DISCARD_CAPABILITY = "choice.affected_player.fixed_controller_discard"
FIXED_CONTEXT_PLAYER_DISCARD_MECHANIC = "fixed-context-player-discard"
FIXED_CONTEXT_PLAYER_DISCARD_CAPABILITY = "trigger.result.fixed_context_player_discard"
_HAND_MECHANIC = "cr-402-hand"
_TARGETS_MECHANIC = "cr-115-targets"
_INSTRUCTION = re.compile(
    r"^(?P<subject>target player|target opponent|each player|each opponent) "
    r"discards (?:(?P<one>a|one) card|(?P<many>two|three) cards)\.?$",
    re.IGNORECASE,
)
_COUNTS = {"a": 1, "one": 1, "two": 2, "three": 3}
_HAND_PREDICATE = ObjectQuerySpec(zones=("hand",))
_CLOSED_PREDICATE = stable_json(_HAND_PREDICATE.to_dict())


class AffectedPlayerDiscardSubject(str, Enum):
    CONTROLLER = "controller"
    TARGET_PLAYER = "target_player"
    TARGET_OPPONENT = "target_opponent"
    EACH_PLAYER = "each_player"
    EACH_OPPONENT = "each_opponent"

    @property
    def targeted(self) -> bool:
        return self in {
            AffectedPlayerDiscardSubject.TARGET_PLAYER,
            AffectedPlayerDiscardSubject.TARGET_OPPONENT,
        }

    @property
    def opponent_only(self) -> bool:
        return self in {
            AffectedPlayerDiscardSubject.TARGET_OPPONENT,
            AffectedPlayerDiscardSubject.EACH_OPPONENT,
        }


_SUBJECTS = {
    "target player": AffectedPlayerDiscardSubject.TARGET_PLAYER,
    "target opponent": AffectedPlayerDiscardSubject.TARGET_OPPONENT,
    "each player": AffectedPlayerDiscardSubject.EACH_PLAYER,
    "each opponent": AffectedPlayerDiscardSubject.EACH_OPPONENT,
}


def fixed_affected_player_discard_predicate_is_closed(
    value: Mapping[str, Any],
) -> bool:
    try:
        predicate = ObjectQuerySpec.from_dict(value)
    except (TypeError, ValueError):
        return False
    return stable_json(predicate.to_dict()) == _CLOSED_PREDICATE


@dataclass(frozen=True, slots=True)
class FixedAffectedPlayerDiscardTemplate:
    subject: AffectedPlayerDiscardSubject
    count: int

    def __post_init__(self) -> None:
        if not isinstance(self.subject, AffectedPlayerDiscardSubject):
            raise ValueError("Affected-player discard subject is malformed")
        supported_counts = (
            {1, 2, 3, 4}
            if self.subject == AffectedPlayerDiscardSubject.CONTROLLER
            else {1, 2, 3}
        )
        if self.count not in supported_counts:
            raise ValueError("Affected-player discard count is unsupported")

    @property
    def template_id(self) -> str:
        return (
            f"fixed-affected-player-discard-{self.subject.value}-"
            f"{self.count}-v1"
        )

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        players: str | list[str] = {
            AffectedPlayerDiscardSubject.CONTROLLER: ["$controller"],
            AffectedPlayerDiscardSubject.EACH_PLAYER: "all",
            AffectedPlayerDiscardSubject.EACH_OPPONENT: "opponents",
        }.get(self.subject, ["$target.0"])
        effect: dict[str, Any] = {
            "op": "choose_cards_apnap",
            "actor": "$controller",
            "players": players,
            "zone": "hand",
            "predicate": _HAND_PREDICATE.to_dict(),
            "count": self.count,
            "then": "discard",
            "hidden": True,
            "prompt": "Choose the required card(s) to discard.",
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
            FIXED_AFFECTED_PLAYER_DISCARD_MECHANIC,
            _HAND_MECHANIC,
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


def fixed_affected_player_discard_effect_template(
    text: str,
) -> FixedAffectedPlayerDiscardTemplate | None:
    """Lower one mandatory fixed affected-player discard instruction."""

    normalized = " ".join(text.strip().split())
    match = _INSTRUCTION.fullmatch(normalized)
    if match is None:
        return None
    count_word = (match.group("one") or match.group("many")).casefold()
    return FixedAffectedPlayerDiscardTemplate(
        subject=_SUBJECTS[match.group("subject").casefold()],
        count=_COUNTS[count_word],
    )


def fixed_controller_discard_effect_template(
    text: str,
) -> FixedAffectedPlayerDiscardTemplate | None:
    """Lower one fixed controller discard only for closed effect sequences."""

    normalized = " ".join(text.strip().split())
    match = re.fullmatch(
        rf"(?:you )?discard (?P<count>{FIXED_COUNT_PATTERN}) cards?\.?",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return None
    count = fixed_number(match.group("count"))
    if count not in {1, 2, 3, 4}:
        return None
    return FixedAffectedPlayerDiscardTemplate(
        subject=AffectedPlayerDiscardSubject.CONTROLLER,
        count=count,
    )


def standalone_controller_discard_effect_template(text: str):
    """Expose the closed controller leaf through its own producer contract."""
    template = fixed_controller_discard_effect_template(text)
    if template is None:
        return None
    identity, effects, schema, mechanics = template.compiled()
    return identity, effects, schema, (*mechanics, FIXED_CONTROLLER_DISCARD_MECHANIC)


def damage_player_discard_result(binding, body: str):
    """Bind an untargeted discard to a normalized damaged-player occurrence."""
    if binding.event.value not in {"damage.dealt", "damage.dealt.self"}:
        return None

    def guarantees_player(condition):
        if not isinstance(condition, Mapping):
            return False
        if condition == {"field": "target_kind", "op": "eq", "value": "player"}:
            return True
        children = condition.get("all", ())
        return isinstance(children, (list, tuple)) and any(guarantees_player(child) for child in children)

    if not guarantees_player(binding.event_condition):
        return None
    match = re.fullmatch(r"(?:that player|they) discards? (?P<body>.+)", body, re.I)
    if match is None:
        return None
    template = fixed_controller_discard_effect_template("Discard " + match["body"])
    if template is None or template.count > 3:
        return None
    effect = deepcopy(dict(template.effects[0]))
    effect["players"] = ["$context.target"]
    return (
        "fixed-damage-player-discard-result-v1", (effect,), None,
        (*template.mechanics, FIXED_CONTEXT_PLAYER_DISCARD_MECHANIC),
    ), False


__all__ = [
    "AffectedPlayerDiscardSubject",
    "FIXED_AFFECTED_PLAYER_DISCARD_CAPABILITY",
    "FIXED_AFFECTED_PLAYER_DISCARD_MECHANIC",
    "FixedAffectedPlayerDiscardTemplate",
    "fixed_affected_player_discard_effect_template",
    "fixed_affected_player_discard_predicate_is_closed",
    "fixed_controller_discard_effect_template",
]
