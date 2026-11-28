"""Exact same-player artifact sacrifice and return."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


ARTIFACT_SACRIFICE_RETURN_CAPABILITY = "zone.move.same_player_artifact_sacrifice_return"
ARTIFACT_SACRIFICE_RETURN_MECHANIC = "same-player-artifact-sacrifice-return"
ARTIFACT_SACRIFICE_RETURN_OPERATION = "exchange_artifact_zones"
_REQUIREMENTS = (ARTIFACT_SACRIFICE_RETURN_CAPABILITY, "target.revalidate_resolution",
                 "zone.move.fixed_public_set", "zone.change.destination_replacement")


def artifact_sacrifice_return_payload():
    return {"op": ARTIFACT_SACRIFICE_RETURN_OPERATION, "schema_version": 1,
            "battlefield_card": "$target.0", "graveyard_card": "$target.1"}


def artifact_sacrifice_return_targets():
    return {"groups": [
        {"id": "battlefield_artifact", "zones": ["battlefield"], "categories": ["permanent"],
         "types_any": ["artifact"], "count": 1},
        {"id": "graveyard_artifact", "zones": ["graveyard"], "categories": ["card"],
         "types_any": ["artifact"], "count": 1},
    ], "same_player_groups": [["battlefield_artifact", "graveyard_artifact"]]}


def uses_artifact_sacrifice_return(effect):
    return effect.get("op") == ARTIFACT_SACRIFICE_RETURN_OPERATION and "schema_version" in effect


def artifact_sacrifice_return_dependencies(effects, target_schema):
    if (len(effects) != 1 or type(effects[0].get("schema_version")) is not int
            or FrozenMap(effects[0]) != FrozenMap(artifact_sacrifice_return_payload())
            or not isinstance(target_schema, Mapping)
            or FrozenMap(target_schema) != FrozenMap(artifact_sacrifice_return_targets())
            or any(type(group.get("count")) is not int for group in target_schema["groups"])):
        return ()
    return _REQUIREMENTS


def artifact_sacrifice_return_runtime_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if uses_artifact_sacrifice_return(effect))
    if not marked and ARTIFACT_SACRIFICE_RETURN_CAPABILITY not in program.capability_dependencies:
        return ()
    required = artifact_sacrifice_return_dependencies(program.effects, program.target_schema)
    if (not required or tuple(program.effects) != marked
            or (program.event, program.active_zone) != ("activate", "battlefield")
            or not isinstance(program.cost_schema, Mapping) or program.cost_schema.get("tap_source") is not True
            or program.event_condition is not None or len(program.handlers) != 1):
        raise ValueError("Simultaneous artifact sacrifice and return needs both exact targets and its paid tap activation")
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler

    ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    if ability.mana_ability or not ability.tap_source or ability.builtin_semantic_key != program.key:
        raise ValueError("Artifact sacrifice and return differs from its original activation")
    return required


def is_closed_artifact_sacrifice_return_program(program):
    try:
        required = artifact_sacrifice_return_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
