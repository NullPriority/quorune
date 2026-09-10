from __future__ import annotations

"""Capability closure for fixed public-origin zone-move effects."""

from typing import Any, Iterable, Mapping, Sequence

from ..attachment_references import (
    AttachmentReferenceError,
    AttachmentReferenceKind,
    AttachmentReferenceSpec,
)
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..public_zone_moves import (
    FIXED_OWNER_ZONE_MOVE_CAPABILITY,
    FIXED_OWNER_ZONE_MOVE_MECHANIC,
    PublicZoneMoveError,
    PublicZoneMoveSetSpec,
    PublicZoneSeatRelation,
)
from ..targets import TargetGroup
from ..util import stable_json
from .graveyard_card_targets import (
    GraveyardCardTargetError,
    PublicGraveyardCardTargetSpec,
)


_PLAYER_TARGETS = (
    {
        "zones": ["player"],
        "categories": ["player"],
        "count": 1,
        "player_relation": "any",
    },
    {
        "zones": ["player"],
        "categories": ["player"],
        "count": 1,
        "player_relation": "opponent",
    },
)


def _target_schema_values() -> frozenset[str]:
    base = {
        "zones": ["battlefield"],
        "categories": ["permanent"],
        "count": 1,
    }
    schemas = [base]
    for values in (
        ("artifact",),
        ("artifact", "creature"),
        ("artifact", "enchantment"),
        ("artifact", "creature", "enchantment"),
        ("creature",),
        ("creature", "land"),
        ("enchantment",),
        ("land",),
    ):
        schemas.append({**base, "types_any": list(values)})
    schemas.extend(
        (
            {
                **base,
                "characteristic_forms_any": [
                    {
                        "types_all": ["creature"],
                        "subtypes_any": [],
                        "supertypes_any": [],
                    },
                    {
                        "types_all": [],
                        "subtypes_any": ["vehicle"],
                        "supertypes_any": [],
                    },
                ],
            },
            {
                **base,
                "types_none": ["land"],
                "characteristic_forms_any": [
                    {
                        "types_all": ["artifact"],
                        "subtypes_any": [],
                        "supertypes_any": [],
                    },
                    {
                        "types_all": [],
                        "subtypes_any": [],
                        "supertypes_any": ["legendary"],
                    },
                    {
                        "types_all": [],
                        "subtypes_any": ["saga"],
                        "supertypes_any": [],
                    },
                ],
            },
            {
                "zones": ["graveyard"],
                "categories": ["card"],
                "count": 1,
            },
            {
                "zones": ["graveyard"],
                "categories": ["card"],
                "types_any": ["artifact", "instant", "sorcery"],
                "count": 1,
            },
            {
                "zones": ["exile"],
                "categories": ["card"],
                "face_down": False,
                "count": 1,
            },
        )
    )
    return frozenset(stable_json(value) for value in schemas)


_OWNER_ZONE_TARGET_SCHEMAS = _target_schema_values()


def fixed_owner_zone_move_covered_mechanics(
    capability_ids: Iterable[str],
) -> tuple[str, ...]:
    if FIXED_OWNER_ZONE_MOVE_CAPABILITY not in set(capability_ids):
        return ()
    return (
        FIXED_OWNER_ZONE_MOVE_MECHANIC,
        "fixed-public-zone-move",
        "return-to-owner-hand",
    )


def _closed_owner_zone_target_schema(value: Mapping[str, Any] | None) -> bool:
    if not isinstance(value, Mapping):
        return False
    try:
        TargetGroup.from_mapping(value)
    except (TypeError, ValueError):
        return False
    return stable_json(dict(value)) in _OWNER_ZONE_TARGET_SCHEMAS


def _closed_return_choice(effect: Mapping[str, Any]) -> bool:
    if set(effect) != {
        "op",
        "actor",
        "players",
        "zone",
        "predicate",
        "count",
        "then",
        "prompt",
    } or (
        effect.get("op") != "choose_cards_apnap"
        or effect.get("actor") != "$controller"
        or effect.get("zone") != "battlefield"
        or effect.get("count") != 1
        or effect.get("then") != "return_owner_hand"
        or type(effect.get("prompt")) is not str
        or not effect.get("prompt")
    ):
        return False
    try:
        predicate = ObjectQuerySpec.from_dict(effect["predicate"])
    except (KeyError, TypeError, ObjectQueryError):
        return False
    players = effect.get("players")
    expected = (
        ObjectQuerySpec(
            zones=("battlefield",),
            types_all=("creature",),
        )
        if players == "all"
        else ObjectQuerySpec(
            zones=("battlefield",),
            types_all=("land",),
            tapped=True,
        )
        if players == ["$controller"]
        else None
    )
    return bool(expected is not None and predicate == expected)


def fixed_owner_zone_move_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize the closed owner-destination single-object move family."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if FIXED_OWNER_ZONE_MOVE_MECHANIC not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    if _closed_return_choice(effect):
        return (
            (FIXED_OWNER_ZONE_MOVE_CAPABILITY,)
            if target_schema is None
            else ()
        )
    operation = effect.get("op")
    if operation not in {"move", "shuffle_into_library"}:
        return ()
    card = effect.get("card")
    targeted = card == "$target.0"
    attached = isinstance(card, Mapping)
    if targeted is not _closed_owner_zone_target_schema(target_schema):
        return ()
    if not targeted and target_schema is not None:
        return ()
    if attached:
        try:
            attachment = AttachmentReferenceSpec.from_dict(card)
        except (AttachmentReferenceError, TypeError):
            return ()
        if (
            attachment.relation is not AttachmentReferenceKind.ENCHANTED
            or attachment.required_card_type != "creature"
        ):
            return ()
    elif not targeted and card != "$source.zone_object":
        return ()

    if operation == "shuffle_into_library":
        if set(effect) != {"op", "card"} or not attached:
            return ()
    else:
        expected = {"op", "card", "destination"}
        destination = effect.get("destination")
        if destination == "library":
            expected.add("position")
            if effect.get("position") not in {"top", "bottom", 2, 3, 4}:
                return ()
        elif destination not in {"graveyard", "hand"}:
            return ()
        if set(effect) != expected:
            return ()
        if targeted and isinstance(target_schema, Mapping):
            zones = target_schema.get("zones")
            if (
                zones == ["graveyard"]
                and destination != "library"
                or zones == ["exile"]
                and destination != "graveyard"
                or zones == ["battlefield"]
                and destination not in {"hand", "library"}
            ):
                return ()
    return (
        FIXED_OWNER_ZONE_MOVE_CAPABILITY,
        *(("target.revalidate_resolution",) if targeted else ()),
        *(("attachment.reference.current_or_lki",) if attached else ()),
    )


def public_graveyard_card_exile_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    mechanics = {str(value).casefold() for value in mechanic_ids}
    if not {
        "exile",
        "fixed-public-zone-move",
        "cr-115-targets",
    }.issubset(mechanics):
        return ()
    if len(effects) != 1 or target_schema is None:
        return ()
    try:
        PublicGraveyardCardTargetSpec.from_target_schema(target_schema)
    except (GraveyardCardTargetError, TypeError):
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "card"}
        or effect.get("op") != "exile_public_graveyard_card"
        or effect.get("card") != "$target.0"
    ):
        return ()
    return (
        "card.exile.public_graveyard",
        "target.revalidate_resolution",
    )


def fixed_public_zone_move_set_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    mechanics = {str(value).casefold() for value in mechanic_ids}
    if not {
        "fixed-public-zone-move",
        "fixed-public-zone-move-set",
    }.issubset(mechanics) or len(effects) != 1:
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "source", "set"}
        or effect.get("op") != "move_public_zone_set"
        or effect.get("source") != "$source"
    ):
        return ()
    try:
        spec = PublicZoneMoveSetSpec.from_dict(effect["set"])
    except (KeyError, TypeError, PublicZoneMoveError):
        return ()
    targeted = spec.seat_relation is PublicZoneSeatRelation.TARGET_PLAYER
    if targeted:
        if (
            "cr-115-targets" not in mechanics
            or dict(target_schema or {}) not in _PLAYER_TARGETS
            or spec.target_seat != "$target.0"
        ):
            return ()
        return (
            "zone.move.fixed_public_set",
            "target.revalidate_resolution",
        )
    if target_schema is not None or "cr-115-targets" in mechanics:
        return ()
    return ("zone.move.fixed_public_set",)


__all__ = [
    "fixed_owner_zone_move_covered_mechanics",
    "fixed_owner_zone_move_node_capabilities",
    "fixed_public_zone_move_set_node_capabilities",
    "public_graveyard_card_exile_node_capabilities",
]
