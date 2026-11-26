"""Bind a paid cast window to one selected owned graveyard artifact."""

from collections.abc import Mapping

from ..replacement.immutable import thaw_value
from .effect_shape_walkers import contains_nested_key
from .owner_library_position_shapes import has_exact_tap_price


ARTIFACT_GRAVEYARD_CAST_CAPABILITY = "casting.permission.activated_artifact_graveyard"
ARTIFACT_GRAVEYARD_CAST_MECHANIC = "activation-owned-artifact-graveyard-cast"
_ARTIFACT_GRAVEYARD_CAST_VERSION_FIELD = "artifact_graveyard_cast_version"


def artifact_graveyard_cast_target():
    return {
        "zones": ["graveyard"], "categories": ["card"], "owner": "you",
        "artifact": True, "count": 1,
    }


def _legacy_artifact_graveyard_cast_effect():
    return {
        "op": "grant_cast_permission", "card": "$target.0", "zone": "graveyard",
        "owned_only": True, "types_any": ["Artifact"], "source": "$source",
    }


def artifact_graveyard_cast_effect():
    return {**_legacy_artifact_graveyard_cast_effect(), _ARTIFACT_GRAVEYARD_CAST_VERSION_FIELD: 1}


def _is_exact_artifact_target(schema):
    return (
        isinstance(schema, Mapping) and thaw_value(schema) == artifact_graveyard_cast_target()
        and type(schema["count"]) is int and schema["artifact"] is True
    )


def artifact_graveyard_cast_dependencies(effects, target_schema, active_zone, cost_schema):
    if (
        active_zone != "battlefield" or not has_exact_tap_price(cost_schema)
        or not _is_exact_artifact_target(target_schema)
        or thaw_value(effects) != [artifact_graveyard_cast_effect()]
        or type(effects[0][_ARTIFACT_GRAVEYARD_CAST_VERSION_FIELD]) is not int
        or effects[0]["owned_only"] is not True
    ):
        return ()
    return ARTIFACT_GRAVEYARD_CAST_CAPABILITY, "target.revalidate_resolution"


def artifact_graveyard_cast_runtime_dependencies(program):
    if (
        ARTIFACT_GRAVEYARD_CAST_CAPABILITY not in program.capability_dependencies
        and ARTIFACT_GRAVEYARD_CAST_MECHANIC not in program.coverage
        and not contains_nested_key((program.effects, program.handlers), _ARTIFACT_GRAVEYARD_CAST_VERSION_FIELD)
    ):
        return ()

    from ..compiler.activated_costs import activated_ability_cost
    from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors

    required = artifact_graveyard_cast_dependencies(
        program.effects, program.target_schema, program.active_zone, program.cost_schema,
    )
    if (
        not required or program.event != "activate" or program.event_condition is not None
        or program.trigger_limit is not None or program.provenance.get("granted_only")
        or len(program.handlers) != 1
    ):
        raise ValueError("Artifact graveyard casting requires one original tap ability and owned artifact target")
    abilities = activated_abilities_from_descriptors(program.handlers)
    if len(abilities) != 1:
        raise ValueError("Artifact graveyard casting requires the ordinary activation catalog")
    ability, = abilities
    if (
        ability.builtin_semantic_key != program.key or ability.zones != ("battlefield",)
        or ability.mana_ability or ability.sorcery_speed or ability.activation_conditions
        or not _is_exact_artifact_target(ability.target_schema)
        or not has_exact_tap_price(activated_ability_cost(ability))
    ):
        raise ValueError("Artifact graveyard casting lost its exact activation price or target")
    return required


def reviewed_artifact_graveyard_cast_result_matches(current, old):
    """Compare the complete current result with the exact old grant instruction."""
    if (
        not _is_exact_artifact_target(old.target_schema)
        or thaw_value(old.effects) != [_legacy_artifact_graveyard_cast_effect()]
        or old.effects[0]["owned_only"] is not True
    ):
        return False
    try:
        required = artifact_graveyard_cast_runtime_dependencies(current)
    except (KeyError, TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(current.capability_dependencies)
