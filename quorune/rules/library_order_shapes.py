"""Separate exact leaf proof for ordering and shuffle capabilities."""
from collections.abc import Mapping

from ..library_order_model import (
    FixedTopLibraryOrderSpec,
    TOP_ORDER_OPERATION,
    TOP_ORDER_CAPABILITY,
    TOP_ORDER_MECHANIC,
    SHUFFLE_OPERATION,
    SHUFFLE_CAPABILITY,
    SHUFFLE_MECHANIC,
    validate_fixed_shuffle_effect,
)


def fixed_library_order_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or len(effects) != 1:
        return ()
    effect = effects[0]
    if not isinstance(effect, Mapping):
        return ()
    try:
        if effect.get('op') == TOP_ORDER_OPERATION and TOP_ORDER_MECHANIC in mechanic_ids:
            FixedTopLibraryOrderSpec.from_effect(effect)
            return (TOP_ORDER_CAPABILITY,) if effect['player'] == '$controller' else ()
        if effect.get('op') == SHUFFLE_OPERATION and SHUFFLE_MECHANIC in mechanic_ids:
            validate_fixed_shuffle_effect(effect)
            return (SHUFFLE_CAPABILITY,) if effect['player'] == '$controller' else ()
    except (TypeError, ValueError):
        return ()
    return ()


def library_order_payload_dependencies(value):
    if not isinstance(value, Mapping):
        return ()
    if value.get('op') == TOP_ORDER_OPERATION:
        FixedTopLibraryOrderSpec.from_effect(value)
        if value['player'] != '$controller':
            raise ValueError('Top order program requires reserved controller')
        return (TOP_ORDER_CAPABILITY,)
    if value.get('op') == SHUFFLE_OPERATION:
        validate_fixed_shuffle_effect(value)
        if value['player'] != '$controller':
            raise ValueError('Shuffle program requires reserved controller')
        return (SHUFFLE_CAPABILITY,)
    return ()


def is_closed_fixed_library_order_program(program):
    """Prove the leaf shape before provisional programs are promoted."""
    required = fixed_library_order_capabilities(
        effects=program.effects,
        target_schema=program.target_schema,
        mechanic_ids=program.coverage,
    )
    return bool(required) and set(required) <= set(program.capability_dependencies)
