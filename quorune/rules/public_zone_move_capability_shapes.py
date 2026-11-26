from __future__ import annotations

"""Capability closure for fixed public-origin zone-move effects."""

from typing import Any, Iterable, Mapping, Sequence
from .linked_exile_return_shapes import linked_exile_return_node_capabilities
from ..linked_exile_return_model import LINKED_EXILE_RETURN_CAPABILITY, LINKED_EXILE_RETURN_MECHANIC

from ..affected_permanents import (
    AffectedPermanentSetError,
    AffectedPermanentSetSpec,
    DEFENDING_PLAYER_UNTAP_SET_FIELD,
    TARGET_PLAYER_CREATURE_UNTAP_SNAPSHOT_FIELD,
    decode_target_player_creature_untap_snapshot,
    target_player_creature_snapshot_target_schema,
    PermanentControllerRelation,
    decode_defending_player_creature_untap,
)
from ..attachment_references import (
    AttachmentReferenceError,
    AttachmentReferenceKind,
    AttachmentReferenceSpec,
)
from ..object_predicate import ObjectQueryError, ObjectQuerySpec, object_query_capabilities
from ..public_zone_moves import (
    ANNOUNCED_GRAVEYARD_RETURN_CAPABILITY, ANNOUNCED_GRAVEYARD_RETURN_VERSION,
    ANNOUNCED_MANA_VALUE_MAX_FIELD, PUBLIC_AURA_ENTRY_VERSIONS,
    SAME_TURN_GRAVEYARD_RETURN_VERSIONS, TARGETED_SAME_TURN_GRAVEYARD_RETURN_VERSION, PUBLIC_CHOSEN_GRAVEYARD_RETURN_VERSIONS,
    same_turn_graveyard_return_history_query, targeted_same_turn_graveyard_return_schema,
    OWN_GRAVEYARD_TYPE_CHOICE_FIELD,
    own_graveyard_type_return_dependencies,
    PUBLIC_GRAVEYARD_POWER_RETURN_CAPABILITY,
    PUBLIC_GRAVEYARD_POWER_RETURN_VERSION,
    FIXED_OWNER_ZONE_MOVE_CAPABILITY,
    FIXED_OWNER_ZONE_MOVE_MECHANIC,
    owned_permanent_library_bottom_target_schema,
    OWNER_LIBRARY_POSITION_CAPABILITY, OWNER_LIBRARY_POSITION_MECHANIC, OWNER_LIBRARY_POSITION_OPERATION,
    GRAVEYARD_LIBRARY_TARGET_CAPABILITY,
    GRAVEYARD_LIBRARY_TARGET_FIELD,
    PublicZoneMoveError,
    PublicZoneDestination,
    PublicZoneMoveSetSpec,
    PublicZoneOrigin,
    PublicZoneSeatRelation,
    stack_spell_exile_node_capabilities,
)
from ..replacement.immutable import FrozenMap, thaw_value
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
    from .public_exile_result_model import ATTACKING_EXILE_SEARCH_CAPABILITY, ATTACKING_EXILE_SEARCH_MECHANIC

    from ..event_card_return import EVENT_HAND_RETURN_CAPABILITY, EVENT_HAND_RETURN_MECHANIC

    if EVENT_HAND_RETURN_CAPABILITY in supplied:
        covered.add(EVENT_HAND_RETURN_MECHANIC)
    if ATTACKING_EXILE_SEARCH_CAPABILITY in supplied:
        covered.add(ATTACKING_EXILE_SEARCH_MECHANIC)
    if LINKED_EXILE_RETURN_CAPABILITY in supplied:
        covered.update((LINKED_EXILE_RETURN_MECHANIC, "cr-115-targets"))
    if OWNER_LIBRARY_POSITION_CAPABILITY in supplied:
        covered.add(OWNER_LIBRARY_POSITION_MECHANIC)
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


def face_up_exile_owner_shuffle_is_closed(effect, target_schema):
    return (dict(effect) == {"op": "shuffle_into_library", "card": "$target.0"}
            and _closed_owner_zone_target_schema(target_schema)
            and tuple(target_schema["zones"]) == ("exile",))


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

    if len(effects) == 1 and effects[0].get("op") == OWNER_LIBRARY_POSITION_OPERATION:
        from .owner_library_position_shapes import owner_library_position_node_capabilities

        return owner_library_position_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
        )
    mechanics = {str(value).casefold() for value in mechanic_ids}
    linked = linked_exile_return_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    if linked:
        return linked
    from ..event_card_return import (
        event_hand_return_node_capabilities, event_library_move_capabilities,
        is_event_hand_return, is_event_library_move, validate_event_library_move,
    )

    if len(effects) == 1 and is_event_hand_return(effects[0]):
        return event_hand_return_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
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
    from .opponent_spell_or_permanent_return import ENTRY_FIELD, FIELD, opponent_spell_or_permanent_return_capabilities

    if FIELD in effect or ENTRY_FIELD in effect:
        return opponent_spell_or_permanent_return_capabilities(effect, target_schema)
    if target_schema == owned_permanent_library_bottom_target_schema():
        expected = {"op": "move", "card": "$target.0", "destination": "library", "position": "bottom"}
        return (FIXED_OWNER_ZONE_MOVE_CAPABILITY, "target.revalidate_resolution") if effect == expected else ()
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
        from .target_recipient_results import target_recipient_prefix_is_closed

        face_up_exile_target = face_up_exile_owner_shuffle_is_closed(effect, target_schema)
        if set(effect) != {"op", "card"} or not (
            attached or face_up_exile_target
            or targeted and target_recipient_prefix_is_closed(effect, target_schema, "owner")
        ):
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
    from ..graveyard_card_result_model import GRAVEYARD_CARD_COUNTER_LIFE_OPERATION
    if effect.get('op') == GRAVEYARD_CARD_COUNTER_LIFE_OPERATION:
        from .graveyard_card_results import graveyard_card_counter_life_node_capabilities
        return graveyard_card_counter_life_node_capabilities(effects=effects, target_schema=target_schema)
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


def _mixed_zone_exile_payload_dependencies(value: Mapping[str, Any]) -> tuple[str, ...]:
    from ..public_zone_moves import FixedMixedZoneExileSpec, MIXED_ZONE_EXILE_CAPABILITY

    spec = FixedMixedZoneExileSpec.from_dict(value["set"])
    if value != spec.effect():
        raise ValueError("Mixed-zone exile requires its exact original source and set")
    return ("zone.move.fixed_public_set", MIXED_ZONE_EXILE_CAPABILITY)


def public_zone_move_set_payload_dependencies(value: Mapping[str, Any]) -> tuple[str, ...]:
    """Require the explicit owners of the versioned public zone sets."""
    from ..public_zone_moves import is_mixed_zone_exile_spec

    if is_mixed_zone_exile_spec(value["set"]):
        return _mixed_zone_exile_payload_dependencies(value)
    spec = PublicZoneMoveSetSpec.from_dict(value["set"])
    history = (
        same_turn_graveyard_return_history_query(spec.seat_relation).capability_dependencies
        if spec.schema_version in SAME_TURN_GRAVEYARD_RETURN_VERSIONS else ()
    )
    return (
        "zone.move.fixed_public_set",
        *history,
        *object_query_capabilities(spec.query),
        *((PUBLIC_GRAVEYARD_POWER_RETURN_CAPABILITY,)
          if spec.schema_version == PUBLIC_GRAVEYARD_POWER_RETURN_VERSION else ()),
        *(("attachment.aura.typed_restriction",) if spec.schema_version in PUBLIC_AURA_ENTRY_VERSIONS
            and spec.destination is PublicZoneDestination.BATTLEFIELD else ()),
        *((ANNOUNCED_GRAVEYARD_RETURN_CAPABILITY,) if spec.schema_version == ANNOUNCED_GRAVEYARD_RETURN_VERSION else ()),
    )


def public_zone_move_set_runtime_dependencies(value: Mapping[str, Any]) -> tuple[str, ...]:
    """Do not retroactively add payload dependencies to historical set versions."""
    raw = value["set"]
    from ..public_zone_moves import is_mixed_zone_exile_spec

    if is_mixed_zone_exile_spec(raw):
        return _mixed_zone_exile_payload_dependencies(value)
    if isinstance(raw, Mapping) and raw.get("schema_version") == ANNOUNCED_GRAVEYARD_RETURN_VERSION:
        from ..query_effect_amount_model import CastXAmountSpec, DECLARED_AMOUNT_CAPABILITY

        if (set(value) != {"op", "source", "set"} or value.get("source") != "$source"
                or CastXAmountSpec.from_dict(raw.get(ANNOUNCED_MANA_VALUE_MAX_FIELD)) != CastXAmountSpec()):
            raise ValueError("A graveyard return requires the spell's announced X")
        projected = {**value, "set": {**raw, ANNOUNCED_MANA_VALUE_MAX_FIELD: 2}}
        return (*public_zone_move_set_payload_dependencies(projected), DECLARED_AMOUNT_CAPABILITY)
    spec = PublicZoneMoveSetSpec.from_dict(raw)
    if spec.schema_version in PUBLIC_CHOSEN_GRAVEYARD_RETURN_VERSIONS:
        raise ValueError("Chosen graveyard return is generated only by its original public choice")
    return public_zone_move_set_payload_dependencies(value) if spec.schema_version in {
        *PUBLIC_AURA_ENTRY_VERSIONS, PUBLIC_GRAVEYARD_POWER_RETURN_VERSION,
    } else ()


CHOSEN_COLOR_PERMANENT_RETURN_TEMPLATE = "chosen-color-permanent-return-v1"


def chosen_color_permanent_return_effect() -> dict[str, Any]:
    from ..compiler.source_protection_choices import COLORS

    branches = {}
    for symbol, _ in COLORS:
        spec = PublicZoneMoveSetSpec(
            query=ObjectQuerySpec(zones=("battlefield",), colors_any=(symbol,)),
            origin=PublicZoneOrigin.BATTLEFIELD, destination=PublicZoneDestination.OWNER_HAND,
        )
        branches[symbol] = [{"op": "move_public_zone_set", "source": "$source", "set": spec.to_dict()}]
    return {
        "op": "choose_option", "player": "$controller", "prompt": "Choose a color.",
        "options": [{"id": symbol, "label": label} for symbol, label in COLORS],
        "then_by_choice": branches,
    }


def chosen_color_permanent_return_capabilities(effect, target_schema, mechanics):
    expected = chosen_color_permanent_return_effect()
    if (target_schema is not None or "cr-115-targets" in mechanics
            or stable_json(effect) != stable_json(expected)):
        return ()
    return tuple(sorted({
        capability
        for branch in expected["then_by_choice"].values()
        for capability in public_zone_move_set_payload_dependencies(branch[0])
    }))


def chosen_color_permanent_return_runtime_dependencies(program):
    if program.provenance.get("template_id") != CHOSEN_COLOR_PERMANENT_RETURN_TEMPLATE:
        return ()
    if ((program.event, program.active_zone) != ("resolve", "stack")
            or len(program.effects) != 1 or program.target_schema is not None
            or program.cost_schema is not None or program.handlers):
        raise ValueError("A chosen-color return requires one ordinary nontargeted spell effect")
    required = chosen_color_permanent_return_capabilities(program.effects[0], program.target_schema, program.coverage)
    if not required:
        raise ValueError("A chosen-color return requires one exact public return set for each of the five colors")
    return required


CHOSEN_TYPE_TAP_STATE_TEMPLATE = "chosen-type-target-player-tap-state-v1"
CHOSEN_TYPE_TAP_STATE_FIELD = "chosen_type_tap_state"
CHOSEN_TYPE_TAP_STATE_TARGET = {
    "zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "any",
}
CHOSEN_TYPE_TAP_STATE_MECHANICS = (
    "tap-and-untap", "fixed-public-tap-state-set", "cr-115-targets",
)


def _target_player_tap_set(card_type, tapped):
    return {
        "op": "set_public_tap_state", "source": "$source", "tapped": tapped,
        "set": AffectedPermanentSetSpec(
            query=ObjectQuerySpec(zones=("battlefield",), types_all=(card_type,), tapped=not tapped),
            controller_relation=PermanentControllerRelation.TARGET_PLAYER, target_controller="$target.0",
        ).to_dict(),
    }


def chosen_type_tap_state_effect():
    types = ("artifact", "creature", "land")
    return {
        "op": "choose_option", "player": "$controller", CHOSEN_TYPE_TAP_STATE_FIELD: 1,
        "prompt": "Choose artifact, creature, or land.",
        "options": [{"id": kind, "label": kind.title()} for kind in types],
        "then_by_choice": {
            kind: [{
                "op": "choose_option", "player": "$controller",
                "prompt": "Tap or untap the chosen type?",
                "options": [{"id": "tap", "label": "Tap"}, {"id": "untap", "label": "Untap"}],
                "then_by_choice": {
                    "tap": [_target_player_tap_set(kind, True)],
                    "untap": [_target_player_tap_set(kind, False)],
                },
            }]
            for kind in types
        },
    }


def chosen_type_tap_state_capabilities(effect, target_schema, mechanics):
    expected = chosen_type_tap_state_effect()
    if (
        not set(CHOSEN_TYPE_TAP_STATE_MECHANICS) <= set(mechanics)
        or stable_json(target_schema) != stable_json(CHOSEN_TYPE_TAP_STATE_TARGET)
        or stable_json(effect) != stable_json(expected)
    ):
        return ()
    dependencies = set()
    for options in expected["then_by_choice"].values():
        for result in options[0]["then_by_choice"].values():
            owned = fixed_public_tap_state_set_node_capabilities(
                effects=result, target_schema=target_schema, mechanic_ids=mechanics,
            )
            if not owned:
                return ()
            dependencies.update(owned)
    return tuple(sorted(dependencies))


def targeted_same_turn_graveyard_return_runtime_dependencies(program):
    if "zone.move.fixed_public_set" not in program.capability_dependencies:
        return ()
    from .effect_shape_walkers import nested_effects

    if not any(
        effect.get("op") == "move_public_zone_set" and isinstance(effect.get("set"), Mapping)
        and effect["set"].get("schema_version") == TARGETED_SAME_TURN_GRAVEYARD_RETURN_VERSION
        for effect in nested_effects((program.effects, program.handlers))
    ):
        return ()
    if (program.event, program.active_zone) != ("resolve", "stack") or program.handlers or len(program.effects) != 1:
        raise ValueError("Targeted same-turn graveyard return requires its original resolving spell")
    required = fixed_public_zone_move_set_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("Targeted same-turn graveyard return requires its original current arrival target schema")
    return required


def chosen_type_tap_state_runtime_dependencies(program):
    if not any(CHOSEN_TYPE_TAP_STATE_FIELD in effect for effect in program.effects) and (
        program.provenance.get("template_id") != CHOSEN_TYPE_TAP_STATE_TEMPLATE
    ):
        return ()
    if (
        (program.event, program.active_zone) != ("resolve", "stack")
        or not program.ability_id.startswith("spell:")
        or len(program.effects) != 1 or program.cost_schema is not None or program.handlers
    ):
        raise ValueError("A chosen type tap-state set requires one complete resolving spell instruction")
    required = chosen_type_tap_state_capabilities(program.effects[0], program.target_schema, program.coverage)
    if not required:
        raise ValueError("A chosen type tap-state set requires six exact results and the original target player")
    return required


def fixed_public_zone_move_set_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    from ..public_zone_moves import is_mixed_zone_exile_spec

    if (len(effects) == 1 and effects[0].get("op") == "move_public_zone_set"
            and is_mixed_zone_exile_spec(effects[0].get("set"))):
        if target_schema is not None:
            return ()
        try:
            return _mixed_zone_exile_payload_dependencies(effects[0])
        except (ValueError, TypeError, KeyError):
            return ()
    mechanic_ids = tuple(mechanic_ids)
    stack_exile = stack_spell_exile_node_capabilities(
        effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
    )
    if stack_exile:
        return stack_exile
    from .private_hand_entry_shapes import private_hand_collection_node_capabilities
    collection = private_hand_collection_node_capabilities(
        effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
    )
    if collection:
        return collection
    private_hand = fixed_private_hand_entry_node_capabilities(
        effects=effects,
        target_schema=target_schema,
        mechanic_ids=mechanic_ids,
    )
    if private_hand:
        return private_hand
    if len(effects) == 1 and CHOSEN_TYPE_TAP_STATE_FIELD in effects[0]:
        return chosen_type_tap_state_capabilities(effects[0], target_schema, mechanic_ids)
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
    if effect.get("op") == "choose_option":
        color_return = chosen_color_permanent_return_capabilities(effect, target_schema, mechanics)
        if color_return:
            return color_return
    from ..public_zone_moves import PUBLIC_SAME_NAME_MOVE_TARGET_FIELD
    raw_set = effect.get("set")
    if isinstance(raw_set, Mapping) and PUBLIC_SAME_NAME_MOVE_TARGET_FIELD in raw_set:
        from .same_name_zone_moves import same_name_zone_move_node_capabilities

        return same_name_zone_move_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
        )
    if OWN_GRAVEYARD_TYPE_CHOICE_FIELD in effect:
        if target_schema is not None or "cr-115-targets" in mechanics:
            return ()
        try:
            return own_graveyard_type_return_dependencies(effect)
        except (TypeError, ValueError):
            return ()
    from .public_exile_result_model import ATTACKING_EXILE_SEARCH_MECHANIC, PUBLIC_EXILE_RESULT_MECHANIC, PublicExileFollowup

    has_followup = "followup" in effect
    supplied_followup = {PUBLIC_EXILE_RESULT_MECHANIC, ATTACKING_EXILE_SEARCH_MECHANIC} & mechanics
    if len(supplied_followup) != int(has_followup):
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
            if followup.schema_version == 2:
                if supplied_followup != {ATTACKING_EXILE_SEARCH_MECHANIC} or dict(target_schema or {}) != _PLAYER_TARGETS[0]:
                    return ()
            elif supplied_followup != {PUBLIC_EXILE_RESULT_MECHANIC}:
                return ()
            result_caps = followup.capability_dependencies
    except (KeyError, TypeError, ValueError):
        return ()
    if spec.schema_version in PUBLIC_CHOSEN_GRAVEYARD_RETURN_VERSIONS:
        return ()
    dependencies = (*public_zone_move_set_payload_dependencies(effect), *result_caps)
    if spec.schema_version == TARGETED_SAME_TURN_GRAVEYARD_RETURN_VERSION:
        from ..target_history import GRAVEYARD_BATTLEFIELD_ARRIVAL_FIELD

        if (
            spec.target_refs != "$targets" or "cr-115-targets" not in mechanics
            or thaw_value(FrozenMap(target_schema or {})) != targeted_same_turn_graveyard_return_schema()
            or type(target_schema.get(GRAVEYARD_BATTLEFIELD_ARRIVAL_FIELD)) is not bool
        ):
            return ()
        return (*dependencies, "target.revalidate_resolution")
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
        HAND_ENTRY_FOLLOWUP_CAPABILITY, HAND_ENTRY_SUBTYPE_LIFE_CAPABILITIES, VERSIONED_HAND_ENTRY_FIELDS,
        HandEntrySubtypeLife, decode_hand_entry_followup,
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
    from ..hand_entry_queries import decode_hand_entry_queries, hand_entry_query_capabilities
    try:
        decode_hand_entry_queries(effect["query"])
        followup = decode_hand_entry_followup(effect)
        attacking = decode_hand_attacking_entry(effect)
    except (KeyError, TypeError, ValueError):
        return ()
    base = ("zone.move.fixed_private_hand_choice", "zone.change.destination_replacement", *hand_entry_query_capabilities(effect["query"]))
    if attacking:
        return (*base, ATTACKING_CARD_ENTRY_CAPABILITY)
    if followup is None:
        return base
    if isinstance(followup, HandEntrySubtypeLife):
        return (*base, *HAND_ENTRY_SUBTYPE_LIFE_CAPABILITIES)
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
    from ..affected_permanents import COMBAT_PLAYER_LAND_TAP_FIELD, decode_combat_player_land_tap

    if COMBAT_PLAYER_LAND_TAP_FIELD in effects[0]:
        if target_schema is not None or "cr-115-targets" in mechanics:
            return ()
        try:
            group = decode_combat_player_land_tap(effects[0])
        except (TypeError, ValueError):
            return ()
        return ("permanent.tap_state.fixed_set", "trigger.event.normalized_damage",
                *object_query_capabilities(group.query))
    if DEFENDING_PLAYER_UNTAP_SET_FIELD in effects[0]:
        if target_schema is not None or "cr-115-targets" in mechanics:
            return ()
        try:
            group = decode_defending_player_creature_untap(effects[0])
        except (TypeError, ValueError, KeyError):
            return ()
        return ("permanent.tap_state.fixed_set", "trigger.event.normalized_self_attack",
            *object_query_capabilities(group.query))

    from ..affected_permanents import (
        TARGET_UNSPENT_MANA_FIELD, decode_target_unspent_mana_tap, target_unspent_mana_target_schema,
    )

    if TARGET_PLAYER_CREATURE_UNTAP_SNAPSHOT_FIELD in effects[0]:
        if ("cr-115-targets" not in mechanics or type((target_schema or {}).get("count")) is not int
                or target_schema != target_player_creature_snapshot_target_schema()):
            return ()
        try:
            spec = decode_target_player_creature_untap_snapshot(effects[0])
        except (TypeError, AffectedPermanentSetError):
            return ()
        return ("permanent.tap_state.fixed_set", "target.revalidate_resolution",
                "untap.step.prohibit_next_controller", *object_query_capabilities(spec.query))
    if TARGET_UNSPENT_MANA_FIELD in effects[0]:
        if ("cr-115-targets" not in mechanics or type((target_schema or {}).get("count")) is not int
                or target_schema != target_unspent_mana_target_schema()):
            return ()
        try:
            spec = decode_target_unspent_mana_tap(effects[0])
        except (TypeError, AffectedPermanentSetError):
            return ()
        return ("permanent.tap_state.fixed_set", "target.revalidate_resolution",
                *object_query_capabilities(spec.query))

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
