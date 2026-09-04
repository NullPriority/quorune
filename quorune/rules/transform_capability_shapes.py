from __future__ import annotations

"""Capability closure for source-pinned permanent transformation."""

from typing import Any, Iterable, Mapping, Sequence

from ..spell_history_transform_model import SPELL_HISTORY_TRANSFORM_MECHANIC_ID


PERMANENT_TRANSFORM_CAPABILITY_ID = "permanent.transform.face_change"


def source_transform_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        SPELL_HISTORY_TRANSFORM_MECHANIC_ID not in mechanics
        or target_schema is not None
        or len(effects) != 1
    ):
        return ()
    effect = effects[0]
    if set(effect) != {
        "op",
        "card",
        "expected_transform_count",
    } or effect != {
        "op": "transform",
        "card": "$source.zone_object",
        "expected_transform_count": "$source.transform_count",
    }:
        return ()
    return (PERMANENT_TRANSFORM_CAPABILITY_ID,)


__all__ = [
    "PERMANENT_TRANSFORM_CAPABILITY_ID",
    "source_transform_node_capabilities",
]
