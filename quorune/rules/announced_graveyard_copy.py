"""Announced mana-value targeting and named persistent creature copies."""

from __future__ import annotations

from collections.abc import Mapping

from ..activation_x import ANNOUNCED_ACTIVATION_X_CAPABILITY, exact_mana_only_x_activation_cost
from ..replacement.immutable import FrozenMap, thaw_value
from .graveyard_card_targets import PublicGraveyardCardTargetKind, PublicGraveyardCardTargetSpec


ANNOUNCED_GRAVEYARD_COPY_VERSION = "announced_graveyard_copy_version"
ANNOUNCED_GRAVEYARD_COPY_TEMPLATE = "announced-graveyard-self-copy-v1"
ANNOUNCED_GRAVEYARD_COPY_CAPABILITY = "continuous.resolution.announced_graveyard_self_copy"
ANNOUNCED_GRAVEYARD_COPY_DEPENDENCIES = (
    ANNOUNCED_GRAVEYARD_COPY_CAPABILITY, ANNOUNCED_ACTIVATION_X_CAPABILITY, "target.revalidate_resolution",
)


def owned_graveyard_creature_target_schema() -> dict:
    return PublicGraveyardCardTargetSpec(
        PublicGraveyardCardTargetKind.CREATURE_CARD, owner_relation="you",
    ).to_target_schema()


def announced_graveyard_copy_target_schema(announced_x: int) -> dict:
    """Apply the announced value through ordinary graveyard target validation."""
    if type(announced_x) is not int or announced_x < 0:
        raise ValueError("A graveyard copy target requires a nonnegative announced X")
    return {**owned_graveyard_creature_target_schema(), "mana_value": announced_x}


def announced_graveyard_copy_effect(name: str) -> dict:
    if type(name) is not str or not name or name.strip() != name:
        raise ValueError("A named copy requires its exact retained name")
    return {
        "op": "copy_until_end_of_turn", ANNOUNCED_GRAVEYARD_COPY_VERSION: 1,
        "source": "$source.zone_object", "target": "$target.0", "retained_name": name,
    }


def is_announced_graveyard_copy_effect(effect) -> bool:
    return isinstance(effect, Mapping) and ANNOUNCED_GRAVEYARD_COPY_VERSION in effect


def is_announced_graveyard_copy_program(program) -> bool:
    return program is not None and any(is_announced_graveyard_copy_effect(effect) for effect in program.effects)


def validate_announced_graveyard_copy_effect(effect, *, resolved=False):
    if (not is_announced_graveyard_copy_effect(effect)
            or type(effect[ANNOUNCED_GRAVEYARD_COPY_VERSION]) is not int
            or effect[ANNOUNCED_GRAVEYARD_COPY_VERSION] != 1):
        raise ValueError("A named graveyard copy requires its supported descriptor version")
    expected = announced_graveyard_copy_effect(effect.get("retained_name"))
    if not resolved:
        if dict(effect) != expected:
            raise ValueError("A named graveyard copy has a closed source and target")
        return
    if (set(effect) - {"_runtime_source", "reason"} != set(expected)
            or effect["op"] != expected["op"] or type(effect["target"]) is not str or not effect["target"]
            or (effect["source"] is not None and (type(effect["source"]) is not str or not effect["source"]))):
        raise ValueError("A resolved graveyard copy requires its original source and announced target")


def announced_graveyard_copy_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or "cr-115-targets" not in mechanic_ids:
        return ()
    try:
        validate_announced_graveyard_copy_effect(effects[0])
    except (TypeError, ValueError):
        return ()
    if thaw_value(target_schema) != owned_graveyard_creature_target_schema():
        return ()
    return ANNOUNCED_GRAVEYARD_COPY_DEPENDENCIES


def announced_graveyard_copy_runtime_dependencies(program):
    if not is_announced_graveyard_copy_program(program):
        return ()
    required = announced_graveyard_copy_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=("cr-115-targets",),
    )
    if (not required or program.event != "activate" or program.active_zone != "battlefield"
            or not exact_mana_only_x_activation_cost(program.cost_schema)):
        raise ValueError("Announced graveyard copy requires one ordinary X battlefield activation")
    return required


def is_closed_announced_graveyard_copy_program(program) -> bool:
    try:
        required = announced_graveyard_copy_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def prepare_announced_graveyard_copy_values(host, target, *, name, retained_ability) -> FrozenMap:
    """Keep the printed copy exceptions in the same copiable characteristics."""
    from ..ability_fragments import ability_fragment_to_dict
    from ..card_program_faces import selected_face_id
    from ..characteristic_evaluation import render_layered_type_line, type_parts
    from ..defined_token_faces import single_face_copy_values
    from ..entry_copy import COPIABLE_FACE_FIELD, COPIABLE_ORACLE_FIELD, copiable_oracle_id
    from ..token_copy_recipes import TokenCopyExceptionSpec, apply_copy_exception

    values = single_face_copy_values(host._copyable_characteristics(target))
    values[COPIABLE_ORACLE_FIELD] = copiable_oracle_id(target)
    values[COPIABLE_FACE_FIELD] = selected_face_id(host.card_record(target), target)
    exception = TokenCopyExceptionSpec(
        schema_version=3, name=name, add_ability_fragments=(ability_fragment_to_dict(retained_ability),),
    )
    result = apply_copy_exception(values, exception)
    card_types, subtypes, supertypes = type_parts(result["type_line"])
    if "legendary" not in supertypes:
        result["type_line"] = render_layered_type_line({
            "card_types": card_types, "subtypes": sorted(subtypes), "supertypes": supertypes | {"legendary"},
        })
    return FrozenMap(result)
