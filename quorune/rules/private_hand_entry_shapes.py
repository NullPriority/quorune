from __future__ import annotations

"""Capability closure for the complete versioned private collection payload."""

from ..object_predicate import object_query_capabilities
from ..private_hand_entry_model import (
    PRIVATE_HAND_COLLECTION_CAPABILITY, PRIVATE_HAND_COLLECTION_MECHANICS,
    decode_private_hand_collection,
)


def private_hand_collection_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) != 1 or target_schema is not None
            or not set(PRIVATE_HAND_COLLECTION_MECHANICS) <= set(mechanic_ids)):
        return ()
    try:
        spec = decode_private_hand_collection(effects[0])
    except (KeyError, TypeError, ValueError):
        return ()
    return (PRIVATE_HAND_COLLECTION_CAPABILITY, "zone.move.fixed_private_hand_choice",
            "zone.change.destination_replacement", "attachment.aura.typed_restriction",
            *object_query_capabilities(spec.query))
