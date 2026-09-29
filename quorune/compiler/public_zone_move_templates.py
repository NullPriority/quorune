from __future__ import annotations

"""Closed Oracle grammar for fixed public-origin zone moves."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..object_predicate import ObjectQuerySpec
from ..public_zone_moves import (
    PublicBattlefieldController,
    PublicZoneDestination,
    PublicZoneMoveSetSpec,
    PublicZoneOrigin,
    PublicZoneRelationAxis,
    PublicZoneSeatRelation,
)
from ..rules.graveyard_card_targets import (
    PublicGraveyardCardTargetKind,
    PublicGraveyardCardTargetSpec,
)
from .destruction_templates import fixed_affected_permanent_query
from .direct_target import compiled_direct_target, direct_target_effect
from .public_tap_state_set_templates import (
    FixedPublicTapStateSetTemplate,
    fixed_public_tap_state_set_effect_template,
)


def _player_target_schema(*, opponent: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "zones": ["player"],
        "categories": ["player"],
        "count": 1,
        "player_relation": "opponent" if opponent else "any",
    }
    return result


@dataclass(frozen=True, slots=True)
class PublicGraveyardCardExileTemplate:
    """One target physical card in any public graveyard."""

    target: PublicGraveyardCardTargetKind = PublicGraveyardCardTargetKind.CARD

    def __post_init__(self) -> None:
        if not isinstance(self.target, PublicGraveyardCardTargetKind):
            raise ValueError("Public graveyard exile target is unsupported")

    @property
    def template_id(self) -> str:
        return (
            "exile-target-"
            + PublicGraveyardCardTargetSpec(self.target).slug
            + "-from-public-graveyard-v1"
        )

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return direct_target_effect(
            "exile_public_graveyard_card",
            reference_field="card",
        )

    @property
    def target_schema(self) -> Mapping[str, Any]:
        return PublicGraveyardCardTargetSpec(self.target).to_target_schema()

    @property
    def mechanics(self) -> tuple[str, ...]:
        return ("exile", "fixed-public-zone-move", "cr-115-targets")

    def compiled(self):
        return compiled_direct_target(
            template_id=self.template_id,
            effects=self.effects,
            target_schema=self.target_schema,
            mechanics=self.mechanics,
        )


@dataclass(frozen=True, slots=True)
class PublicZoneMoveSetTemplate:
    spec: PublicZoneMoveSetSpec
    target_schema: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.spec, PublicZoneMoveSetSpec):
            raise ValueError("Public zone move requires a typed affected set")
        schema = dict(self.target_schema) if self.target_schema is not None else None
        targeted = self.spec.seat_relation is PublicZoneSeatRelation.TARGET_PLAYER
        if targeted is not (schema is not None):
            raise ValueError(
                "Public zone-move target schema contradicts its affected set"
            )
        object.__setattr__(self, "target_schema", schema)

    @property
    def template_id(self) -> str:
        return f"move-fixed-public-set-{self.spec.fingerprint[:16]}-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "move_public_zone_set",
                "source": "$source",
                "set": self.spec.to_dict(),
            },
        )

    @property
    def mechanics(self) -> tuple[str, ...]:
        mechanic = (
            "exile"
            if self.spec.destination is PublicZoneDestination.EXILE
            else "return-to-owner-hand"
            if self.spec.destination is PublicZoneDestination.OWNER_HAND
            else "fixed-public-zone-move"
        )
        return (
            mechanic,
            "fixed-public-zone-move",
            "fixed-public-zone-move-set",
            *(("cr-115-targets",) if self.target_schema is not None else ()),
        )

    def compiled(self):
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


def _relation(
    phrase: str,
) -> tuple[
    PublicZoneRelationAxis,
    PublicZoneSeatRelation,
    str | None,
    Mapping[str, Any] | None,
] | None:
    normalized = phrase.casefold()
    mapping = {
        "": (
            PublicZoneRelationAxis.CONTROLLER,
            PublicZoneSeatRelation.ANY,
            None,
            None,
        ),
        " you control": (
            PublicZoneRelationAxis.CONTROLLER,
            PublicZoneSeatRelation.ACTOR,
            None,
            None,
        ),
        " your opponents control": (
            PublicZoneRelationAxis.CONTROLLER,
            PublicZoneSeatRelation.OPPONENTS,
            None,
            None,
        ),
        " target player controls": (
            PublicZoneRelationAxis.CONTROLLER,
            PublicZoneSeatRelation.TARGET_PLAYER,
            "$target.0",
            _player_target_schema(),
        ),
        " target opponent controls": (
            PublicZoneRelationAxis.CONTROLLER,
            PublicZoneSeatRelation.TARGET_PLAYER,
            "$target.0",
            _player_target_schema(opponent=True),
        ),
        " you own": (
            PublicZoneRelationAxis.OWNER,
            PublicZoneSeatRelation.ACTOR,
            None,
            None,
        ),
        " your opponents own": (
            PublicZoneRelationAxis.OWNER,
            PublicZoneSeatRelation.OPPONENTS,
            None,
            None,
        ),
        " target player owns": (
            PublicZoneRelationAxis.OWNER,
            PublicZoneSeatRelation.TARGET_PLAYER,
            "$target.0",
            _player_target_schema(),
        ),
        " target opponent owns": (
            PublicZoneRelationAxis.OWNER,
            PublicZoneSeatRelation.TARGET_PLAYER,
            "$target.0",
            _player_target_schema(opponent=True),
        ),
    }
    return mapping.get(normalized)


def public_graveyard_card_exile_template(
    text: str,
) -> PublicGraveyardCardExileTemplate | None:
    targets = "|".join(
        re.escape(kind.value) for kind in PublicGraveyardCardTargetKind
    )
    match = re.fullmatch(
        rf"exile target (?P<target>{targets}) from a graveyard\.?",
        text.strip(),
        re.IGNORECASE,
    )
    if match is None:
        return None
    return PublicGraveyardCardExileTemplate(
        PublicGraveyardCardTargetKind(match.group("target").casefold())
    )


def _graveyard_set_template(text: str) -> PublicZoneMoveSetTemplate | None:
    normalized = " ".join(text.strip().split()).casefold().rstrip(".")
    relation = None
    target_schema = None
    if normalized in {"exile all graveyards", "exile all cards from all graveyards"}:
        relation = PublicZoneSeatRelation.ANY
    elif normalized == "exile each opponent's graveyard":
        relation = PublicZoneSeatRelation.OPPONENTS
    elif normalized == "exile target player's graveyard":
        relation = PublicZoneSeatRelation.TARGET_PLAYER
        target_schema = _player_target_schema()
    if relation is None:
        return None
    return PublicZoneMoveSetTemplate(
        spec=PublicZoneMoveSetSpec(
            query=ObjectQuerySpec(zones=("graveyard",)),
            origin=PublicZoneOrigin.GRAVEYARD,
            destination=PublicZoneDestination.EXILE,
            relation_axis=PublicZoneRelationAxis.OWNER,
            seat_relation=relation,
            target_seat=(
                "$target.0"
                if relation is PublicZoneSeatRelation.TARGET_PLAYER
                else None
            ),
        ),
        target_schema=target_schema,
    )


def _battlefield_set_template(
    text: str,
    *,
    action: str,
    destination: PublicZoneDestination,
) -> PublicZoneMoveSetTemplate | None:
    match = re.fullmatch(
        rf"{action} (?:all|each) (?P<subject>.+?)"
        r"(?P<relation> target opponent controls| target player controls|"
        r" target opponent owns| target player owns| you control| you own|"
        r" your opponents control| your opponents own)?"
        + (
            r" to (?:their owners['’] hands|their owner['’]s hand|their hand)"
            if destination is PublicZoneDestination.OWNER_HAND
            else ""
        )
        + r"\.?",
        text.strip(),
        re.IGNORECASE,
    )
    if match is None:
        return None
    parsed = fixed_affected_permanent_query(match.group("subject"))
    relation = _relation(match.group("relation") or "")
    if parsed is None or relation is None:
        return None
    query, exclude_source = parsed
    axis, seat_relation, target_seat, target_schema = relation
    return PublicZoneMoveSetTemplate(
        spec=PublicZoneMoveSetSpec(
            query=query,
            origin=PublicZoneOrigin.BATTLEFIELD,
            destination=destination,
            relation_axis=axis,
            seat_relation=seat_relation,
            target_seat=target_seat,
            exclude_source=exclude_source,
        ),
        target_schema=target_schema,
    )


def _battlefield_exile_template(text: str) -> PublicZoneMoveSetTemplate | None:
    return _battlefield_set_template(
        text,
        action="exile",
        destination=PublicZoneDestination.EXILE,
    )


def _battlefield_return_template(text: str) -> PublicZoneMoveSetTemplate | None:
    exception = re.fullmatch(
        r"return all creatures to their owners['’] hands except for "
        r"(?P<subtypes>.+)",
        text.strip().rstrip("."),
        re.IGNORECASE,
    )
    if exception is not None:
        parsed = fixed_affected_permanent_query(
            "creatures except for " + exception.group("subtypes")
        )
        if parsed is None:
            return None
        query, exclude_source = parsed
        return PublicZoneMoveSetTemplate(
            spec=PublicZoneMoveSetSpec(
                query=query,
                origin=PublicZoneOrigin.BATTLEFIELD,
                destination=PublicZoneDestination.OWNER_HAND,
                exclude_source=exclude_source,
            )
        )
    return _battlefield_set_template(
        text,
        action="return",
        destination=PublicZoneDestination.OWNER_HAND,
    )


def _graveyard_card_query(subject: str) -> ObjectQuerySpec | None:
    normalized = " ".join(subject.casefold().split())
    fields: dict[str, Any] = {"zones": ("graveyard",)}
    if normalized.startswith("legendary "):
        fields["supertypes_all"] = ("legendary",)
        normalized = normalized.removeprefix("legendary ")
    elif normalized.startswith("basic "):
        fields["supertypes_all"] = ("basic",)
        normalized = normalized.removeprefix("basic ")
    type_words = {
        "artifact": "artifact",
        "battle": "battle",
        "creature": "creature",
        "enchantment": "enchantment",
        "land": "land",
        "planeswalker": "planeswalker",
    }
    parts = tuple(
        part.strip()
        for part in normalized.replace(", and ", ", ")
        .replace(" and ", ", ")
        .split(",")
    )
    if not parts or any(part not in type_words for part in parts):
        return None
    values = tuple(sorted(type_words[part] for part in parts))
    if len(values) == 1:
        fields["types_all"] = values
    else:
        fields["types_any"] = values
    if fields.get("supertypes_all") == ("basic",) and values != ("land",):
        return None
    return ObjectQuerySpec(**fields)


def _graveyard_public_move_template(
    text: str,
) -> PublicZoneMoveSetTemplate | None:
    normalized = " ".join(text.strip().split())
    ordered_hand = re.fullmatch(
        r"Return to your hand all (?P<subject>.+?) cards in your graveyard\.?",
        normalized,
        re.IGNORECASE,
    )
    if ordered_hand is not None:
        query = _graveyard_card_query(ordered_hand.group("subject"))
        return (
            PublicZoneMoveSetTemplate(
                spec=PublicZoneMoveSetSpec(
                    query=query,
                    origin=PublicZoneOrigin.GRAVEYARD,
                    destination=PublicZoneDestination.OWNER_HAND,
                    relation_axis=PublicZoneRelationAxis.OWNER,
                    seat_relation=PublicZoneSeatRelation.ACTOR,
                    schema_version=2,
                )
            )
            if query is not None
            else None
        )
    match = re.fullmatch(
        r"(?P<verb>Return|Put) all (?P<subject>.+?) cards from "
        r"(?P<scope>your graveyard|all graveyards) "
        r"(?P<direction>to|onto) (?P<destination>your hand|the battlefield)"
        r"(?P<tapped> tapped)?"
        r"(?: under (?P<controller>your|their owners['’]) control)?\.?",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return None
    verb = match.group("verb").casefold()
    direction = match.group("direction").casefold()
    destination_text = match.group("destination").casefold()
    if (verb == "return" and direction != "to") or (
        verb == "put" and direction != "onto"
    ):
        return None
    query = _graveyard_card_query(match.group("subject"))
    if query is None:
        return None
    destination = (
        PublicZoneDestination.OWNER_HAND
        if destination_text == "your hand"
        else PublicZoneDestination.BATTLEFIELD
    )
    types = set((*query.types_all, *query.types_any))
    if destination is PublicZoneDestination.BATTLEFIELD and (
        not types or types.intersection({"artifact", "enchantment"})
    ):
        return None
    scope = match.group("scope").casefold()
    controller = (match.group("controller") or "").casefold().replace("’", "'")
    battlefield_controller = None
    if destination is PublicZoneDestination.BATTLEFIELD:
        battlefield_controller = (
            PublicBattlefieldController.ACTOR
            if controller == "your"
            else PublicBattlefieldController.OWNER
        )
        if scope == "all graveyards" and not controller:
            return None
    elif match.group("tapped") or match.group("controller"):
        return None
    return PublicZoneMoveSetTemplate(
        spec=PublicZoneMoveSetSpec(
            query=query,
            origin=PublicZoneOrigin.GRAVEYARD,
            destination=destination,
            relation_axis=PublicZoneRelationAxis.OWNER,
            seat_relation=(
                PublicZoneSeatRelation.ACTOR
                if scope == "your graveyard"
                else PublicZoneSeatRelation.ANY
            ),
            battlefield_controller=battlefield_controller,
            battlefield_tapped=bool(match.group("tapped")),
            schema_version=2,
        )
    )


def public_zone_move_effect_template(
    text: str,
) -> (
    PublicGraveyardCardExileTemplate
    | PublicZoneMoveSetTemplate
    | FixedPublicTapStateSetTemplate
    | None
):
    return (
        public_graveyard_card_exile_template(text)
        or _graveyard_set_template(text)
        or _graveyard_public_move_template(text)
        or _battlefield_exile_template(text)
        or _battlefield_return_template(text)
        or fixed_public_tap_state_set_effect_template(text)
    )


__all__ = [
    "PublicGraveyardCardExileTemplate",
    "PublicZoneMoveSetTemplate",
    "public_graveyard_card_exile_template",
    "public_zone_move_effect_template",
]
