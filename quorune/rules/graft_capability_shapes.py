from __future__ import annotations

"""Closed source-and-entry identity shape for fixed Graft."""


def graft_node_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or "graft" not in mechanic_ids:
        return ()
    expected = {"op": "offer_graft_counter_movement", "player": "$controller",
                "from_ref": "$source", "from_identity": "$context.source_logical_object_id",
                "to_ref": "$context.card", "to_identity": "$context.graft_recipient_identity"}
    return ("counter.lifecycle.graft",) if len(effects) == 1 and dict(effects[0]) == expected else ()
