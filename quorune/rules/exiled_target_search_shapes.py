from __future__ import annotations

"""Close the linked exile, basic-land selection, joint entry and shuffle."""

from ..exiled_target_library_search import (
    EXILED_TARGET_SEARCH_BINDING,
    EXILED_TARGET_SEARCH_DEPENDENCIES,
    EXILED_TARGET_SEARCH_MECHANIC,
    EXILED_TARGET_SEARCH_VERSION,
    exiled_target_search_effects,
    owned_creature_search_target,
    validate_basic_land_search_with_exiled_target,
)
from ..linked_exile_return_model import validate_linked_instruction
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects


def exiled_target_search_node_capabilities(*, effects, target_schema, mechanic_ids):
    target = thaw_value(target_schema)
    if (
        EXILED_TARGET_SEARCH_MECHANIC not in mechanic_ids
        or target != owned_creature_search_target()
        or any(type(target[field]) is not int for field in ("min", "max"))
        or tuple(thaw_value(effects)) != exiled_target_search_effects()
    ):
        return ()
    try:
        validate_linked_instruction(effects[0])
        validate_basic_land_search_with_exiled_target(effects[1])
        validate_linked_instruction(effects[2])
    except (TypeError, ValueError):
        return ()
    return EXILED_TARGET_SEARCH_DEPENDENCIES


def _uses_combined_entry(program):
    return any(
        (effect.get("op") == "search" and effect.get("schema_version") == EXILED_TARGET_SEARCH_VERSION)
        or effect.get("binding_id") == EXILED_TARGET_SEARCH_BINDING
        for effect in nested_effects((program.effects, program.handlers))
    )


def exiled_target_search_runtime_dependencies(program):
    if not _uses_combined_entry(program):
        return ()
    dependencies = exiled_target_search_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    condition = {"all": [
        {"field": "source_attachment_target_ref", "op": "eq", "value": "$context.source"},
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "combat", "op": "truthy", "value": True},
    ]}
    if (
        not dependencies or (program.event, program.active_zone) != ("damage.dealt", "battlefield")
        or program.cost_schema is not None or program.handlers or thaw_value(program.event_condition) != condition
    ):
        raise ValueError("Combined entry requires the complete equipped combat damage trigger")
    return dependencies
