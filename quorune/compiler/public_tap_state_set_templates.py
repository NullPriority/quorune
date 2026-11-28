from __future__ import annotations

"""Closed grammar for fixed public permanent-set tap-state effects."""

from dataclasses import dataclass
from . import regex_working_set as re
from typing import Any, Mapping

from ..affected_permanents import (
    AffectedPermanentSetSpec,
    PermanentControllerRelation,
    TARGET_OPPONENT_NONLAND_UNTAP_SNAPSHOT_FIELD,
    TARGET_PLAYER_CREATURE_UNTAP_SNAPSHOT_FIELD,
    TARGET_UNSPENT_MANA_FIELD,
    target_player_creature_snapshot_spec,
    target_opponent_nonland_snapshot_spec,
    target_opponent_nonland_snapshot_target_schema,
    target_player_creature_snapshot_target_schema,
    target_unspent_mana_tap_spec,
    target_unspent_mana_target_schema,
)
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
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
    restrain_snapshot_on_player_untap: bool = False
    restrain_snapshot_on_controller_untap: bool = False

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
        if type(self.restrain_snapshot_on_player_untap) is not bool or self.restrain_snapshot_on_player_untap and (
            not self.tapped or self.lose_unspent_mana
            or self.spec != target_player_creature_snapshot_spec("$target.0")
            or self.target_schema != target_player_creature_snapshot_target_schema()
        ):
            raise ValueError("Player-bound untap restraint requires the exact original creature set")
        if type(self.restrain_snapshot_on_controller_untap) is not bool or self.restrain_snapshot_on_controller_untap and (
            not self.tapped or self.lose_unspent_mana or self.restrain_snapshot_on_player_untap
            or self.spec != target_opponent_nonland_snapshot_spec("$target.0")
            or self.target_schema != target_opponent_nonland_snapshot_target_schema()
        ):
            raise ValueError("Controller untap restraint requires the exact original nonland set")

    @property
    def template_id(self) -> str:
        action = "tap" if self.tapped else "untap"
        if self.restrain_snapshot_on_controller_untap:
            return f"{action}-fixed-public-set-{self.spec.fingerprint[:16]}-v4"
        version = 3 if self.restrain_snapshot_on_player_untap else 2 if self.lose_unspent_mana else 1
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
                **({TARGET_PLAYER_CREATURE_UNTAP_SNAPSHOT_FIELD: 1} if self.restrain_snapshot_on_player_untap else {}),
                **({TARGET_OPPONENT_NONLAND_UNTAP_SNAPSHOT_FIELD: 1} if self.restrain_snapshot_on_controller_untap else {}),
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


def combat_recipient_land_tap_untap_result(binding, body):
    if (
        (binding.event.value, binding.variant, binding.active_zone)
        != ("damage.dealt", "one_or_more_controlled_creatures_combat_damage_player", "battlefield")
        or body != binding.body
        or body.casefold() != "tap all lands that player controls and untap all lands you control."
    ):
        return None

    from ..affected_permanents import combat_player_land_tap_effect, fixed_controller_land_untap_effect
    from ..rules.fixed_effect_clause_shapes import FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC

    return ("combat-recipient-land-tap-untap-v1",
            (combat_player_land_tap_effect(), fixed_controller_land_untap_effect()),
            None, ("tap-and-untap", FIXED_PUBLIC_TAP_STATE_SET_MECHANIC, FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC))


def fixed_public_tap_state_set_effect_template(
    text: str,
) -> FixedPublicTapStateSetTemplate | None:
    normalized = " ".join(text.strip().split())
    if normalized.casefold() == (
        "tap all nonland permanents target opponent controls. they don't untap during their controller's next untap step."
    ):
        return FixedPublicTapStateSetTemplate(
            target_opponent_nonland_snapshot_spec("$target.0"), True,
            target_opponent_nonland_snapshot_target_schema(), restrain_snapshot_on_controller_untap=True,
        )
    if normalized.casefold() == (
        "tap all creatures target player controls. those creatures don't untap during that player's next untap step."
    ):
        return FixedPublicTapStateSetTemplate(
            target_player_creature_snapshot_spec("$target.0"), True,
            target_player_creature_snapshot_target_schema(), restrain_snapshot_on_player_untap=True,
        )
    if normalized.casefold() == "tap all lands target player controls and that player loses all unspent mana.":
        return FixedPublicTapStateSetTemplate(
            target_unspent_mana_tap_spec("$target.0"), True, target_unspent_mana_target_schema(),
            lose_unspent_mana=True,
        )
    if normalized.casefold() == "untap each enchanted permanent you control.":
        return FixedPublicTapStateSetTemplate(
            AffectedPermanentSetSpec(
                query=ObjectQuerySpec(zones=("battlefield",), state_predicate=PermanentStatePredicateSpec(enchanted=True)),
                controller_relation=PermanentControllerRelation.ACTOR,
            ),
            False,
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
