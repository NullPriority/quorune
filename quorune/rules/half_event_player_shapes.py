from __future__ import annotations

"""Combine typed half values with the unchanged event-player shape owner."""

from ..event_player_result_values import event_result_value_capabilities
from ..fractional_quantity_model import HALF_LIFE_CAPABILITY, HALF_LIFE_MECHANIC
from .event_player_result_shapes import fixed_event_player_result_node_capabilities


def half_event_player_node_capabilities(*, effects, target_schema, mechanic_ids):
    from .capabilities import capability_dependencies_for_node

    required = fixed_event_player_result_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids)
    if not required or HALF_LIFE_CAPABILITY not in required:
        return ()
    proof = []
    for effect in effects:
        extra = event_result_value_capabilities(effect.get('op'), 'amount', effect.get('amount'))
        # The actual amount remains typed; this literal shell proves only the
        # unchanged event/trigger dependencies through the existing owner.
        proof.append({**effect, 'amount': 1} if extra and HALF_LIFE_CAPABILITY in extra else effect)
    ordinary = capability_dependencies_for_node(effects=proof, target_schema=target_schema,
        mechanic_ids=tuple(value for value in mechanic_ids if value != HALF_LIFE_MECHANIC))
    return tuple(sorted({*ordinary, *required})) if ordinary else ()
