"""Capability gate for the exact source-bound Ring instruction shape."""
from __future__ import annotations

from collections.abc import Mapping

from ..ring_model import RING_MECHANIC, RING_TEMPTATION_CAPABILITY, RING_TEMPTATION_OPERATION


def ring_temptation_node_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or RING_MECHANIC not in mechanic_ids or len(effects) != 1:
        return ()
    effect = effects[0]
    if not isinstance(effect, Mapping) or dict(effect) != {
        'op': RING_TEMPTATION_OPERATION, 'player': '$controller'
    }:
        return ()
    return (RING_TEMPTATION_CAPABILITY,)
