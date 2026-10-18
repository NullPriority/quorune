"""Closed source/entry identity operands for Soulbond's two abilities."""
from __future__ import annotations

from ..soulbond_model import SOULBOND_CAPABILITY, SOULBOND_CHOICE_OPERATION


def soulbond_node_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or 'soulbond' not in mechanic_ids or len(effects) != 1:
        return ()
    for other in (False, True):
        expected = {'op':SOULBOND_CHOICE_OPERATION, 'player':'$controller', 'source_ref':'$source',
            'source_identity':'$context.source_logical_object_id',
            'mode':'other_entry' if other else 'source_entry',
            'entered_ref':'$context.card' if other else None,
            'entered_identity':'$context.card_object_identity' if other else None}
        if dict(effects[0]) == expected:
            return (SOULBOND_CAPABILITY,)
    return ()
