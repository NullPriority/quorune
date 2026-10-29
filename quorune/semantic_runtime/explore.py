from __future__ import annotations

"""Pinned source/LKI support for the single-permanent Explore family."""

from collections.abc import Mapping, MutableMapping
from typing import Any


_LKI_CONTROLLER_KEY = "explore_source_lki_controller"
_EXPLORE_OPERATION = "explore"


def _program_explores_source(program: Any, *, complete: bool = False) -> bool:
    def contains(effects):
        return any(isinstance(effect, Mapping) and (
            effect.get('op') == _EXPLORE_OPERATION and effect.get('card') == '$source'
            or complete and effect.get('op') in {'offer_optional_effect', 'offer_optional_mana_payment'}
            and contains(effect.get('effects', ()))) for effect in effects)
    return bool(
        program is not None
        and contains(program.effects)
    )


def _capture_item(
    host: Any,
    item: Any,
    card: Any,
) -> bool:
    if isinstance(item, MutableMapping):
        source_object_id = item.get("source_object_id")
        semantic_key = item.get("semantic_key")
        context = item.get("context")
        if not isinstance(context, MutableMapping):
            return False
    else:
        source_object_id = item.source_object_id
        semantic_key = item.semantic_key
        context = item.context
    if source_object_id != card.object_id:
        return False
    source_logical_object_id = str(
        context.get("source_logical_object_id") or ""
    )
    if (
        source_logical_object_id
        and source_logical_object_id != card.logical_object_id
    ):
        return False
    if not _program_explores_source(host.semantics.get(semantic_key),
            complete=getattr(host.state, 'explore_action_version', None) == 1):
        return False
    context.setdefault(_LKI_CONTROLLER_KEY, card.controller)
    return True


def capture_explore_source_departure(host: Any, card: Any) -> int:
    """Pin the source controller before one represented explorer departs."""

    captured = sum(_capture_item(host, item, card) for item in host.state.stack)
    for batch in host.state.pending_trigger_batches:
        for group in batch.get("groups", ()):
            for item in group.get("items", ()):
                captured += _capture_item(host, item, card)
    return captured


def explore_source_controller(item: Any, cards: Mapping[str, Any]) -> str:
    """Resolve current controller or the pinned CR 701.44c LKI value."""

    source = cards.get(item.source_object_id or item.card_object_id or "")
    source_logical_object_id = str(
        item.context.get("source_logical_object_id") or ""
    )
    if (
        source is not None
        and source.zone == "battlefield"
        and (
            not source_logical_object_id
            or source.logical_object_id == source_logical_object_id
        )
    ):
        return str(source.controller)
    return str(
        item.context.get(_LKI_CONTROLLER_KEY)
        or item.controller
    )


__all__ = [
    "capture_explore_source_departure",
    "explore_source_controller",
]
