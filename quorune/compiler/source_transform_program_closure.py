from __future__ import annotations

"""Promotion boundary for the closed source transform instruction."""

from ..rules.transform_capability_shapes import source_transform_node_capabilities


def is_closed_source_transform_program(program):
    required=source_transform_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)
