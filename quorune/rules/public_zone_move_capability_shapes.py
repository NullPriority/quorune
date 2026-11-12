from __future__ import annotations

"""Capability closure for fixed public-origin zone-move effects."""

from typing import Any, Iterable, Mapping, Sequence
from .linked_exile_return_shapes import linked_exile_return_node_capabilities
from ..linked_exile_return_model import LINKED_EXILE_RETURN_CAPABILITY, LINKED_EXILE_RETURN_MECHANIC

from ..affected_permanents import (
    AffectedPermanentSetError,
    AffectedPermanentSetSpec,
    PermanentControllerRelation,
)
from ..attachment_references import (
    AttachmentReferenceError,
    AttachmentReferenceKind,
    AttachmentReferenceSpec,
)
from ..object_predicate import ObjectQueryError, ObjectQuerySpec, object_query_capabilities
from ..public_zone_moves import (
    FIXED_OWNER_ZONE_MOVE_CAPABILITY,
    FIXED_OWNER_ZONE_MOVE_MECHANIC,
    GRAVEYARD_LIBRARY_TARGET_CAPABILITY,
    GRAVEYARD_LIBRARY_TARGET_FIELD,
    PublicZoneMoveError,
    PublicZoneMoveSetSpec,
    PublicZoneSeatRelation,
)
from ..targets import TargetGroup
from ..util import stable_json
from .graveyard_card_targets import (
    GraveyardCardTargetError,
    PublicGraveyardCardTargetSpec,
    OwnGraveyardCardTargetSpec,
)
from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed, direct_target_predicate_capabilities


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
    supplied = set(capability_ids)
    covered: set[str] = set()
    if LINKED_EXILE_RETURN_CAPABILITY in supplied:
        covered.update((LINKED_EXILE_RETURN_MECHANIC, "cr-115-targets"))
    if FIXED_OWNER_ZONE_MOVE_CAPABILITY in supplied:
        covered.update(
            {
                FIXED_OWNER_ZONE_MOVE_MECHANIC,
                "fixed-public-zone-move",
                "return-to-owner-hand",
            }
        )
    if "zone.move.fixed_private_hand_choice" in supplied:
        covered.update({"fixed-private-hand-entry", "fixed-public-zone-move"})
    if "permanent.tap_state.fixed_set" in supplied:
        covered.update({"fixed-public-tap-state-set", "tap-and-untap"})
    return tuple(sorted(covered))


def _closed_owner_zone_target_schema(value: Mapping[str, Any] | None) -> bool:
    if not isinstance(value, Mapping):
        return False
    try:
        TargetGroup.from_mapping(value)
    except (TypeError, ValueError):
        return False
    if stable_json(dict(value)) in _OWNER_ZONE_TARGET_SCHEMAS:
        return True
    if value.get("zones") == ["battlefield"]:
        return direct_permanent_target_schema_is_closed(value)
    if value.get("zones") == ["graveyard"]:
        try:
            OwnGraveyardCardTargetSpec.from_target_schema(value)
        except (GraveyardCardTargetError, TypeError, ValueError):
            return False
        return True
    return False


def _closed_graveyard_library_target(effect, target_schema) -> bool:
    if (set(effect) != {"op", "card", "destination", "position", GRAVEYARD_LIBRARY_TARGET_FIELD}
            or type(effect[GRAVEYARD_LIBRARY_TARGET_FIELD]) is not int or effect[GRAVEYARD_LIBRARY_TARGET_FIELD] != 1
            or effect["op"] != "move" or effect["card"] != "$target.0" or effect["destination"] != "library"
            or effect["position"] not in ("top", "bottom") or not isinstance(target_schema, Mapping)):
        return False
    schema = dict(target_schema)
    if "source_exclusion" in schema and schema.pop("source_exclusion") is not True:
        return False
    if "up_to" in schema:
        up_to = schema.pop("up_to")
        if type(up_to) is not int or up_to != 1 or "count" in schema:
            return False
        schema["count"] = 1
    for spec in (OwnGraveyardCardTargetSpec, PublicGraveyardCardTargetSpec):
        try:
            spec.from_target_schema(schema)
        except (GraveyardCardTargetError, TypeError, ValueError):
            continue
        return True
    return False


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
    linked = linked_exile_return_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    if linked:
        return linked
    from ..event_card_return import (
        event_library_move_capabilities, is_event_library_move, validate_event_library_move,
    )

    if len(effects) == 1 and is_event_library_move(effects[0]):
        if target_schema is not None or FIXED_OWNER_ZONE_MOVE_MECHANIC not in mechanics:
            return ()
        try:
            validate_event_library_move(effects[0])
        except (TypeError, ValueError):
            return ()
        return (FIXED_OWNER_ZONE_MOVE_CAPABILITY, *event_library_move_capabilities(effects[0]))
    if FIXED_OWNER_ZONE_MOVE_MECHANIC not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    if GRAVEYARD_LIBRARY_TARGET_FIELD in effect:
        return ((FIXED_OWNER_ZONE_MOVE_CAPABILITY, GRAVEYARD_LIBRARY_TARGET_CAPABILITY, "target.revalidate_resolution")
                if _closed_graveyard_library_target(effect, target_schema) else ())
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
        *(direct_target_predicate_capabilities(target_schema)
          if targeted and direct_permanent_target_schema_is_closed(target_schema) else ()),
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


TRIGGERED_SOURCE_EXILE_MECHANIC = "triggered-source-exile"
_SOURCE_EXILE_BY_ZONE = {
    "battlefield": ("exile_permanent", "$source.zone_object", "permanent.exile.effect"),
    "graveyard": ("exile_public_graveyard_card", "$source.graveyard_object", "card.exile.public_graveyard"),
}


def triggered_source_exile_result(zone):
    operation, reference, _capability = _SOURCE_EXILE_BY_ZONE[zone]
    return {"op": operation, "card": reference}


def triggered_source_exile_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (target_schema is not None or len(effects) != 1
            or TRIGGERED_SOURCE_EXILE_MECHANIC not in mechanic_ids):
        return ()
    return tuple(capability for zone, (_, _, capability) in _SOURCE_EXILE_BY_ZONE.items()
                 if effects[0] == triggered_source_exile_result(zone))


def triggered_source_exile_runtime_dependencies(program):
    marked = TRIGGERED_SOURCE_EXILE_MECHANIC in program.coverage
    if not marked and not any(
        effect.get("op") in ("exile_permanent", "exile_public_graveyard_card")
        and effect.get("card") in ("$source.zone_object", "$source.graveyard_object")
        for effect in program.effects
    ):
        return ()
    zone = program.active_zone
    if (program.event != "creature.dies" or zone not in _SOURCE_EXILE_BY_ZONE
            or program.target_schema is not None or len(program.effects) != 1
            or program.effects[0] != triggered_source_exile_result(zone)):
        raise ValueError("Triggered source exile requires one source-bound creature death result")
    return (_SOURCE_EXILE_BY_ZONE[zone][2],)


def fixed_public_zone_move_set_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    mechanic_ids = tuple(mechanic_ids)
    private_hand = fixed_private_hand_entry_node_capabilities(
        effects=effects,
        target_schema=target_schema,
        mechanic_ids=mechanic_ids,
    )
    if private_hand:
        return private_hand
    public_tap_state = fixed_public_tap_state_set_node_capabilities(
        effects=effects,
        target_schema=target_schema,
        mechanic_ids=mechanic_ids,
    )
    if public_tap_state:
        return public_tap_state
    mechanics = {str(value).casefold() for value in mechanic_ids}
    if not {
        "fixed-public-zone-move",
        "fixed-public-zone-move-set",
    }.issubset(mechanics) or len(effects) != 1:
        return ()
    effect = effects[0]
    from .public_exile_result_model import PUBLIC_EXILE_RESULT_MECHANIC, PublicExileFollowup

    has_followup = "followup" in effect
    if has_followup != (PUBLIC_EXILE_RESULT_MECHANIC in mechanics):
        return ()
    expected = {"op", "source", "set"} | ({"followup"} if has_followup else set())
    if (
        set(effect) != expected
        or effect.get("op") != "move_public_zone_set"
        or effect.get("source") != "$source"
    ):
        return ()
    try:
        spec = PublicZoneMoveSetSpec.from_dict(effect["set"])
        result_caps = ()
        if has_followup:
            followup = PublicExileFollowup.from_dict(effect["followup"])
            followup.validate_move(spec)
            result_caps = followup.capability_dependencies
    except (KeyError, TypeError, ValueError):
        return ()
    dependencies = ("zone.move.fixed_public_set", *object_query_capabilities(spec.query), *result_caps)
    targeted = spec.seat_relation is PublicZoneSeatRelation.TARGET_PLAYER
    if targeted:
        if (
            "cr-115-targets" not in mechanics
            or dict(target_schema or {}) not in _PLAYER_TARGETS
            or spec.target_seat != "$target.0"
        ):
            return ()
        return (*dependencies, "target.revalidate_resolution")
    if target_schema is not None or "cr-115-targets" in mechanics:
        return ()
    return dependencies


def fixed_private_hand_entry_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    from .hand_entry_followup import (
        HAND_ENTRY_FOLLOWUP_CAPABILITY, VERSIONED_HAND_ENTRY_FIELDS, decode_hand_entry_followup,
    )
    from .attacking_card_entry import ATTACKING_CARD_ENTRY_CAPABILITY, decode_hand_attacking_entry
    from .reanimation_followup import battlefield_entry_followup_capabilities

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        not {"fixed-private-hand-entry", "fixed-public-zone-move"}.issubset(
            mechanics
        )
        or len(effects) != 1
        or target_schema is not None
    ):
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "player", "query", "tapped"} | VERSIONED_HAND_ENTRY_FIELDS.intersection(effect)
        or effect.get("op") != "put_card_from_hand"
        or effect.get("player") != "$controller"
        or type(effect.get("tapped")) is not bool
    ):
        return ()
    from ..hand_entry_queries import decode_hand_entry_queries
    try:
        decode_hand_entry_queries(effect["query"])
        followup = decode_hand_entry_followup(effect)
        attacking = decode_hand_attacking_entry(effect)
    except (KeyError, TypeError, ValueError):
        return ()
    base = ("zone.move.fixed_private_hand_choice", "zone.change.destination_replacement")
    if attacking:
        return (*base, ATTACKING_CARD_ENTRY_CAPABILITY)
    if followup is None:
        return base
    return tuple(dict.fromkeys((*base, HAND_ENTRY_FOLLOWUP_CAPABILITY, *battlefield_entry_followup_capabilities(followup))))


def fixed_public_tap_state_set_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        not {"tap-and-untap", "fixed-public-tap-state-set"}.issubset(
            mechanics
        )
        or len(effects) != 1
    ):
        return ()
    from ..source_tap_effect import SOURCE_TAP_UNTAP_CAPABILITY, split_source_tap_untap_restraint
    try:
        effect, restraint = split_source_tap_untap_restraint(effects[0])
    except ValueError:
        return ()
    if (
        set(effect) != {"op", "source", "set", "tapped"}
        or effect.get("op") != "set_public_tap_state"
        or effect.get("source") != "$source"
        or type(effect.get("tapped")) is not bool
    ):
        return ()
    try:
        spec = AffectedPermanentSetSpec.from_dict(effect["set"])
    except (KeyError, TypeError, AffectedPermanentSetError):
        return ()
    dependencies = (
        "permanent.tap_state.fixed_set",
        *object_query_capabilities(spec.query),
        *((SOURCE_TAP_UNTAP_CAPABILITY,) if restraint else ()),
    )
    targeted = (
        spec.controller_relation is PermanentControllerRelation.TARGET_PLAYER
    )
    if targeted:
        if (
            "cr-115-targets" not in mechanics
            or dict(target_schema or {}) not in _PLAYER_TARGETS
            or spec.target_controller != "$target.0"
        ):
            return ()
        return (
            *dependencies,
            "target.revalidate_resolution",
        )
    if target_schema is not None or "cr-115-targets" in mechanics:
        return ()
    return dependencies


__all__ = [
    "fixed_owner_zone_move_covered_mechanics",
    "fixed_owner_zone_move_node_capabilities",
    "fixed_public_zone_move_set_node_capabilities",
    "fixed_private_hand_entry_node_capabilities",
    "fixed_public_tap_state_set_node_capabilities",
    "public_graveyard_card_exile_node_capabilities",
]
