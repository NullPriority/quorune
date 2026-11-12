from __future__ import annotations

"""Close fixed resolution characteristic sets and explicit ability bundles."""

from ..rules.fixed_resolution_characteristic_shapes import fixed_resolution_characteristic_set_node_capabilities
from ..rules.temporary_ability_grant import temporary_quoted_grant_node_capabilities
from ..rules.temporary_keyword_removal import temporary_keyword_removal_capabilities
from ..rules.resolution_characteristic_shapes import fixed_resolution_characteristic_capabilities
from ..resolution_characteristic_model import TARGET_CARD_TYPE_ADDITION_VERSION


def is_closed_resolution_characteristic_program(program):
    if len(program.effects) == 1 and program.effects[0].get("schema_version") == TARGET_CARD_TYPE_ADDITION_VERSION:
        required = fixed_resolution_characteristic_capabilities(
            effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        )
        return bool(required and set(required) <= set(program.capability_dependencies))
    for resolver in (fixed_resolution_characteristic_set_node_capabilities, temporary_quoted_grant_node_capabilities,temporary_keyword_removal_capabilities):
        required = resolver(effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage)
        if required and set(required) <= set(program.capability_dependencies):
            return True
    return False
