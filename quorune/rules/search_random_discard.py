from __future__ import annotations

"""Close the private search, live hand random discard, and following shuffle."""

from ..library_order_model import SHUFFLE_CAPABILITY, SHUFFLE_MECHANIC, fixed_shuffle_effect
from ..library_search_model import (
    FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID,
    PRIVATE_SEARCH_RANDOM_DISCARD_CAPABILITY,
    PRIVATE_SEARCH_RANDOM_DISCARD_MECHANIC,
    PRIVATE_SEARCH_RANDOM_DISCARD_VERSION,
    private_search_before_random_discard_effect,
    validate_private_search_before_random_discard,
)
from ..random_discard import (
    RANDOM_DISCARD_CAPABILITY,
    RANDOM_DISCARD_MECHANIC,
    RANDOM_DISCARD_OPERATION,
    random_discard_node_capabilities,
)
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects
from .library_order_shapes import fixed_library_order_capabilities


def private_search_random_discard_effects():
    return (
        private_search_before_random_discard_effect(),
        {"op": RANDOM_DISCARD_OPERATION, "actor": "$controller", "players": ["$controller"], "count": 1},
        fixed_shuffle_effect(),
    )


def private_search_random_discard_node_capabilities(*, effects, target_schema, mechanic_ids):
    if PRIVATE_SEARCH_RANDOM_DISCARD_MECHANIC not in mechanic_ids or target_schema is not None:
        return ()
    if tuple(thaw_value(effects)) != private_search_random_discard_effects():
        return ()
    try:
        validate_private_search_before_random_discard(effects[0])
    except (TypeError, ValueError):
        return ()
    if not random_discard_node_capabilities(
        effects=(thaw_value(effects[1]),), target_schema=None, mechanic_ids=(RANDOM_DISCARD_MECHANIC,),
    ) or not fixed_library_order_capabilities(
        effects=(effects[2],), target_schema=None, mechanic_ids=(SHUFFLE_MECHANIC,),
    ):
        return ()
    return (
        PRIVATE_SEARCH_RANDOM_DISCARD_CAPABILITY,
        FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID,
        RANDOM_DISCARD_CAPABILITY,
        SHUFFLE_CAPABILITY,
    )


def private_search_random_discard_runtime_dependencies(program):
    if PRIVATE_SEARCH_RANDOM_DISCARD_MECHANIC not in program.coverage and not any(
        effect.get("op") == "search" and effect.get("schema_version") == PRIVATE_SEARCH_RANDOM_DISCARD_VERSION
        for effect in nested_effects((program.effects, program.handlers))
    ):
        return ()
    dependencies = private_search_random_discard_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (
        not dependencies or (program.event, program.active_zone) != ("resolve", "stack")
        or not program.ability_id.startswith("spell:") or program.handlers
        or program.cost_schema is not None or program.event_condition is not None
    ):
        raise ValueError("Private search and random discard require their complete printed spell resolution")
    return dependencies
