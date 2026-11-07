from __future__ import annotations

from collections.abc import Mapping

from ..discover_model import (DISCOVER_OPERATION, NONLAND_SCAN_MECHANIC, NONLAND_SCAN_OPERATION,
                              discover_payload_dependencies)


def discover_node_capabilities(*,effects,target_schema,mechanic_ids):
    if target_schema is not None or len(effects) != 1 or not isinstance(effects[0], Mapping):
        return ()
    operation = effects[0].get('op')
    if type(operation) is not str:
        return ()
    mechanic = {DISCOVER_OPERATION: 'discover', NONLAND_SCAN_OPERATION: NONLAND_SCAN_MECHANIC}.get(operation)
    if mechanic is None or mechanic not in mechanic_ids:
        return ()
    try:
        return discover_payload_dependencies(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()


def is_closed_discover_program(program):
    required=discover_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)
