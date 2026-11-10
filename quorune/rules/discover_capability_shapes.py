from __future__ import annotations

from collections.abc import Mapping

from ..discover_model import (DISCOVER_OPERATION, NONLAND_SCAN_MECHANIC, NONLAND_SCAN_OPERATION,
    QUALIFIED_SCAN_MECHANIC, QUALIFIED_SCAN_OPERATION, QUALIFIED_SCAN_TARGET, discover_payload_dependencies)
from ..replacement.immutable import FrozenMap
from ..fixed_top_exile import (
    CHOSEN_IMPULSE_MECHANIC, CHOSEN_IMPULSE_SCOPE, FIXED_TOP_EXILE_MECHANIC, FIXED_TOP_EXILE_OPERATION,
    OWN_LIBRARY_EXILE_SCOPES, RESOLVING_TOP_PLAY_MECHANIC, RESOLVING_TOP_PLAY_SCOPE, validate_fixed_top_exile,
)


def discover_node_capabilities(*,effects,target_schema,mechanic_ids):
    if len(effects) != 1 or not isinstance(effects[0], Mapping):
        return ()
    operation = effects[0].get('op')
    if type(operation) is not str:
        return ()
    mechanic = {DISCOVER_OPERATION: 'discover', NONLAND_SCAN_OPERATION: NONLAND_SCAN_MECHANIC,
                QUALIFIED_SCAN_OPERATION: QUALIFIED_SCAN_MECHANIC,
                FIXED_TOP_EXILE_OPERATION: FIXED_TOP_EXILE_MECHANIC}.get(operation)
    if operation == FIXED_TOP_EXILE_OPERATION:
        mechanic = {CHOSEN_IMPULSE_SCOPE: CHOSEN_IMPULSE_MECHANIC,
                    RESOLVING_TOP_PLAY_SCOPE: RESOLVING_TOP_PLAY_MECHANIC}.get(effects[0].get("scope"), mechanic)
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


def _legacy_fixed_top_instructions(value):
    if isinstance(value, Mapping):
        if value.get("op") == FIXED_TOP_EXILE_OPERATION and validate_fixed_top_exile(value) not in OWN_LIBRARY_EXILE_SCOPES:
            yield value
        for child in value.values():
            yield from _legacy_fixed_top_instructions(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _legacy_fixed_top_instructions(child)


def validate_fixed_top_exile_program_scope(program, values):
    legacy = tuple(_legacy_fixed_top_instructions(values))
    if not legacy:
        return
    if (len(legacy) != 1 or len(program.effects) != 1 or program.target_schema is not None
            or legacy[0] != program.effects[0]):
        raise ValueError("Fixed top resolving casts require one closed untargeted instruction")
    scope = validate_fixed_top_exile(legacy[0])
    if scope == "cast_event_library" and program.event != "spell.cast":
        raise ValueError("The supplying caster must come from the normalized spell cast event")
