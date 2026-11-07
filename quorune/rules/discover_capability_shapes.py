from __future__ import annotations

from collections.abc import Mapping

from ..discover_model import (DISCOVER_OPERATION, NONLAND_SCAN_MECHANIC, NONLAND_SCAN_OPERATION,
    QUALIFIED_SCAN_MECHANIC, QUALIFIED_SCAN_OPERATION, QUALIFIED_SCAN_TARGET, discover_payload_dependencies)
from ..replacement.immutable import FrozenMap


def discover_node_capabilities(*,effects,target_schema,mechanic_ids):
    if len(effects) != 1 or not isinstance(effects[0], Mapping):
        return ()
    operation = effects[0].get('op')
    if type(operation) is not str:
        return ()
    mechanic = {DISCOVER_OPERATION: 'discover', NONLAND_SCAN_OPERATION: NONLAND_SCAN_MECHANIC,
                QUALIFIED_SCAN_OPERATION: QUALIFIED_SCAN_MECHANIC}.get(operation)
    if mechanic is None or mechanic not in mechanic_ids:
        return ()
    try:
        required = discover_payload_dependencies(effects[0])
        if operation == QUALIFIED_SCAN_OPERATION and effects[0]['supplier'] == '$target.0':
            if not isinstance(target_schema, Mapping) or FrozenMap(target_schema) != QUALIFIED_SCAN_TARGET:
                return ()
            return (*required, 'target.revalidate_resolution')
        return required if target_schema is None else ()
    except (TypeError, ValueError, KeyError):
        return ()


def is_closed_discover_program(program):
    required=discover_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)
