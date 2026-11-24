from __future__ import annotations

"""Closed grammar for fixed public permanent-set tap-state effects."""

from dataclasses import dataclass
from . import regex_working_set as re
from typing import Any, Mapping

from ..affected_permanents import (
    AffectedPermanentSetSpec,
    PermanentControllerRelation,
    TARGET_UNSPENT_MANA_FIELD,
    target_unspent_mana_tap_spec,
    target_unspent_mana_target_schema,
)
from .destruction_templates import fixed_affected_permanent_query


FIXED_PUBLIC_TAP_STATE_SET_CAPABILITY = "permanent.tap_state.fixed_set"
FIXED_PUBLIC_TAP_STATE_SET_MECHANIC = "fixed-public-tap-state-set"


def _player_target_schema(*, opponent: bool) -> dict[str, Any]:
    return {
        "zones": ["player"],
        "categories": ["player"],
        "count": 1,
        "player_relation": "opponent" if opponent else "any",
    }


@dataclass(frozen=True, slots=True)
class FixedPublicTapStateSetTemplate:
    spec: AffectedPermanentSetSpec
    tapped: bool
    target_schema: Mapping[str, Any] | None = None
    lose_unspent_mana: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.spec, AffectedPermanentSetSpec):
            raise ValueError("Public tap-state effects require a typed set")
        if type(self.tapped) is not bool:
            raise ValueError("Public tap-state result must be boolean")
        targeted = (
            self.spec.controller_relation
            is PermanentControllerRelation.TARGET_PLAYER
        )
        if targeted is not (self.target_schema is not None):
            raise ValueError("Public tap-state target shape is inconsistent")
        if type(self.lose_unspent_mana) is not bool or self.lose_unspent_mana and (
            not self.tapped or self.spec != target_unspent_mana_tap_spec("$target.0")
            or self.target_schema != target_unspent_mana_target_schema()
        ):
            raise ValueError("Unspent mana loss requires the closed targeted land tap")

    @property
    def template_id(self) -> str:
        action = "tap" if self.tapped else "untap"
        version = 2 if self.lose_unspent_mana else 1
        return f"{action}-fixed-public-set-{self.spec.fingerprint[:16]}-v{version}"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "set_public_tap_state",
                "source": "$source",
                "set": self.spec.to_dict(),
                "tapped": self.tapped,
                **({TARGET_UNSPENT_MANA_FIELD: 1} if self.lose_unspent_mana else {}),
            },
        )

    @property
    def mechanics(self) -> tuple[str, ...]:
        return (
            "tap-and-untap",
            FIXED_PUBLIC_TAP_STATE_SET_MECHANIC,
            *(("cr-115-targets",) if self.target_schema is not None else ()),
        )

    def compiled(self):
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


def fixed_public_tap_state_set_effect_template(
    text: str,
) -> FixedPublicTapStateSetTemplate | None:
    normalized = " ".join(text.strip().split())
    if normalized.casefold() == "tap all lands target player controls and that player loses all unspent mana.":
        return FixedPublicTapStateSetTemplate(
            target_unspent_mana_tap_spec("$target.0"), True, target_unspent_mana_target_schema(),
            lose_unspent_mana=True,
        )
    match = re.fullmatch(
        r"(?P<action>Tap|Untap) (?P<quantifier>all|each) (?P<subject>.+?)"
        r"(?P<relation> target opponent controls| target player controls|"
        r" you control| you don't control| your opponents control)?\.?",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return None
    parsed = fixed_affected_permanent_query(match.group("subject"),
        singular=match['quantifier'].casefold() == 'each')
    if parsed is None:
        return None
    query, exclude_source = parsed
    relation_text = (match.group("relation") or "").casefold()
    relation = PermanentControllerRelation.ANY
    target_controller = None
    target_schema = None
    if relation_text == " you control":
        relation = PermanentControllerRelation.ACTOR
    elif relation_text in {" your opponents control", " you don't control"}:
        relation = PermanentControllerRelation.OPPONENTS
    elif relation_text in {
        " target player controls",
        " target opponent controls",
    }:
        relation = PermanentControllerRelation.TARGET_PLAYER
        target_controller = "$target.0"
        target_schema = _player_target_schema(
            opponent=relation_text == " target opponent controls"
        )
    return FixedPublicTapStateSetTemplate(
        spec=AffectedPermanentSetSpec(
            query=query,
            controller_relation=relation,
            target_controller=target_controller,
            exclude_source=exclude_source,
        ),
        tapped=match.group("action").casefold() == "tap",
        target_schema=target_schema,
    )


__all__ = [
    "FIXED_PUBLIC_TAP_STATE_SET_CAPABILITY",
    "FIXED_PUBLIC_TAP_STATE_SET_MECHANIC",
    "FixedPublicTapStateSetTemplate",
    "fixed_public_tap_state_set_effect_template",
]
