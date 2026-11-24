"""The typed graveyard target and persistent copy of an exiled card."""

from __future__ import annotations

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap, thaw_value
from .graveyard_card_targets import PublicGraveyardCardTargetKind, PublicGraveyardCardTargetSpec


GRAVEYARD_EXILE_COPY_CAPABILITY = "continuous.resolution.graveyard_exile_self_copy"
GRAVEYARD_EXILE_COPY_VERSION = "graveyard_exile_copy_version"
GRAVEYARD_EXILE_COPY_TEMPLATE = "graveyard-exile-self-copy-v1"
GRAVEYARD_EXILE_COPY_PRICE = (("GENERIC", 1), ("U", 1), ("B", 1))
GRAVEYARD_EXILE_COPY_DEPENDENCIES = (
    GRAVEYARD_EXILE_COPY_CAPABILITY, "target.revalidate_resolution", "zone.change.destination_replacement",
)


def graveyard_exile_copy_target_schema() -> dict:
    return PublicGraveyardCardTargetSpec(PublicGraveyardCardTargetKind.CREATURE_CARD).to_target_schema()


def graveyard_exile_copy_effect() -> dict:
    return {
        "op": "copy_until_end_of_turn", GRAVEYARD_EXILE_COPY_VERSION: 1,
        "source": "$source.zone_object", "target": "$target.0",
    }


def is_graveyard_exile_copy_effect(effect) -> bool:
    return isinstance(effect, Mapping) and GRAVEYARD_EXILE_COPY_VERSION in effect


def validate_graveyard_exile_copy_effect(effect, *, resolved: bool = False) -> None:
    expected = graveyard_exile_copy_effect()
    if (not is_graveyard_exile_copy_effect(effect) or type(effect[GRAVEYARD_EXILE_COPY_VERSION]) is not int
            or effect[GRAVEYARD_EXILE_COPY_VERSION] != 1):
        raise ValueError("The graveyard exile copy requires version one")
    if not resolved:
        if dict(effect) != expected:
            raise ValueError("The graveyard exile copy requires its closed source and target bindings")
        return
    runtime_fields = {"_runtime_source", "reason", "_replacement_selections"}
    if (set(effect) - runtime_fields != set(expected) or effect["op"] != expected["op"]
            or type(effect["target"]) is not str or not effect["target"]
            or effect["source"] is not None and (type(effect["source"]) is not str or not effect["source"])):
        raise ValueError("The resolved exile copy requires its original source and announced card")


def is_graveyard_exile_copy_cost(cost) -> bool:
    expected = {
        "text": "{1}{U}{B}", "mana": {**dict.fromkeys(("GENERIC", *"WUBRGC"), 0), **dict(GRAVEYARD_EXILE_COPY_PRICE)},
        "complex_symbols": [], "tap_source": False, "untap_source": False,
        "discard_source": False, "sacrifice_source": False, "exile_source": False,
        "life_payment": 0, "energy_payment": 0, "loyalty_delta": None,
        "source_counter_removal_cost": None, "choices": [], "uncompiled_costs": [],
    }
    return isinstance(cost, Mapping) and thaw_value(cost) == expected


def graveyard_exile_copy_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) != 1 or "cr-115-targets" not in mechanic_ids
            or thaw_value(target_schema) != graveyard_exile_copy_target_schema()):
        return ()
    try:
        validate_graveyard_exile_copy_effect(effects[0])
    except ValueError:
        return ()
    return GRAVEYARD_EXILE_COPY_DEPENDENCIES


def graveyard_exile_copy_runtime_dependencies(program):
    if not any(is_graveyard_exile_copy_effect(effect) for effect in program.effects):
        return ()
    required = graveyard_exile_copy_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=("cr-115-targets",),
    )
    if (not required or program.event != "activate" or program.active_zone != "battlefield"
            or not is_graveyard_exile_copy_cost(program.cost_schema)):
        raise ValueError("Graveyard exile copy requires its ordinary fixed-mana activation and public creature target")
    return required


def is_closed_graveyard_exile_copy_program(program) -> bool:
    try:
        required = graveyard_exile_copy_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def graveyard_exile_copy_values(host, target, *, receiver, retained_ability) -> FrozenMap:
    from ..ability_fragments import ability_fragment_to_dict
    from ..card_program_faces import selected_face_id
    from ..defined_token_faces import single_face_copy_values
    from ..entry_copy import COPIABLE_FACE_FIELD, COPIABLE_ORACLE_FIELD, copiable_oracle_id
    from ..token_copy_recipes import TokenCopyExceptionSpec, apply_copy_exception
    from .copied_flip_faces import CopiedFlipFaces, capture_copied_flip_faces, current_flipped_status

    exception = TokenCopyExceptionSpec(add_ability_fragments=(ability_fragment_to_dict(retained_ability),))
    faces = capture_copied_flip_faces(host, target)
    if faces is not None:
        copied = CopiedFlipFaces(apply_copy_exception(thaw_value(faces.normal), exception),
                                 apply_copy_exception(thaw_value(faces.alternative), exception))
        return FrozenMap(copied.current_values(flipped=current_flipped_status(host, receiver)))
    values = single_face_copy_values(host._copyable_characteristics(target))
    values[COPIABLE_ORACLE_FIELD] = copiable_oracle_id(target)
    values[COPIABLE_FACE_FIELD] = selected_face_id(host.card_record(target), target)
    return FrozenMap(apply_copy_exception(values, exception))
