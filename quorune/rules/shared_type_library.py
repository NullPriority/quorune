"""Closed shared-type library instruction and canonical source-information facts."""
from collections.abc import Mapping

from .shared_type_library_model import (
    SHARED_TYPE_LIBRARY_OPERATION, SHARED_TYPE_LIBRARY_MECHANIC,
    SHARED_TYPE_LIBRARY_CAPABILITY, SharedTypeLibraryResult,
)
from ..shared_creature_type_model import SharedCreatureTypeObject, canonical_creature_types
from ..continuous_effect_model import ContinuousObjectIdentity


def shared_type_library_payload_dependencies(value):
    if not isinstance(value, Mapping) or value.get("op") != SHARED_TYPE_LIBRARY_OPERATION:
        return ()
    if set(value) != {"op", "player", "result"} or value["player"] != "$controller":
        raise ValueError("Shared-type library instruction must use its resolving controller")
    spec = SharedTypeLibraryResult.from_dict(value["result"])
    return tuple(sorted({*spec.capability_dependencies, "target.ability_source.current_or_lki"}))


def shared_type_library_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or target_schema is not None or SHARED_TYPE_LIBRARY_MECHANIC not in mechanic_ids:
        return ()
    try:
        return shared_type_library_payload_dependencies(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()


def materialize_shared_type_library_source(host, item, effect):
    if effect.get("op") != SHARED_TYPE_LIBRARY_OPERATION:
        return None
    if item is None or item.kind not in {"activated_ability", "triggered_ability"}:
        raise ValueError("Shared-type library inspection requires its original ability source")
    from ..target_source import stack_targeting_source, TargetingSourceBinding, TARGETING_SOURCE_CONTEXT
    raw = item.context.get(TARGETING_SOURCE_CONTEXT)
    binding = TargetingSourceBinding.from_dict(raw) if raw is not None else None
    source_id = item.source_object_id
    logical = item.context.get("source_logical_object_id")
    if binding is None or binding.object_id != source_id or binding.logical_object_id != logical or binding.zone != "battlefield":
        raise ValueError("Shared-type library source incarnation is not captured")
    facts = stack_targeting_source(host, item)
    if facts.subtypes is None:
        raise ValueError("Shared-type library source characteristics are unavailable")
    return SharedCreatureTypeObject(ContinuousObjectIdentity(source_id, logical),
        canonical_creature_types(facts.subtypes))


def is_closed_shared_type_library_program(program):
    required = shared_type_library_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required) <= set(program.capability_dependencies)
