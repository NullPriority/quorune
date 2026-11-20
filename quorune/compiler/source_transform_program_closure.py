from __future__ import annotations

"""Promotion boundary for the closed source transform instruction."""

from ..rules.transform_capability_shapes import source_transform_node_capabilities


def is_closed_source_transform_program(program):
    from ..rules.subtype_transform import subtype_transform_node_capabilities
    context=dict(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    required=source_transform_node_capabilities(**context) or subtype_transform_node_capabilities(**context)
    return bool(required) and set(required)<=set(program.capability_dependencies)
