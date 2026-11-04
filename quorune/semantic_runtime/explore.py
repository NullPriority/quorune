from __future__ import annotations

"""Pinned source/LKI support for the single-permanent Explore family."""

from collections.abc import Mapping, MutableMapping
from typing import Any
from ..explore_action_model import CREATURE_EXPLORE_EVENT


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


def materialize_explore_source_types(host, item, effect):
    """Reuse the canonical ability current/LKI snapshot without new saved data."""
    if item is None or effect.get('op') != 'explore' or item.kind not in {'activated_ability', 'triggered_ability'}:
        return None
    from ..target_source import stack_targeting_source
    types = stack_targeting_source(host, item).card_types
    return tuple(sorted(types)) if types is not None else None


def explored_object_types(query, ref, logical_object_id):
    """Read this incarnation, falling back only to that same source's LKI."""
    explorer = query.object(ref)
    if (explorer is not None and explorer.zone == 'battlefield' and not explorer.phased_out
            and explorer.logical_object_id == logical_object_id):
        return tuple(sorted(set(explorer.types)))
    source = getattr(query, 'resolution_source', None)
    if source is not None and source.card_ref == ref and source.logical_object_id == logical_object_id:
        return getattr(query, 'explore_source_types', None)
    return None


def commit_explore_completion(host, intent):
    """Publish legacy completion and the qualified event in one trigger batch."""
    from ..trigger_processing import enqueue_trigger_batch
    explorer = next((card for card in host.state.cards.values() if card.ref == intent.explorer_ref), None)
    changed_objects = ([explorer.object_id] if explorer is not None
        and explorer.logical_object_id == intent.explorer_logical_object_id else [])
    details = {'player': intent.player, 'explorer': intent.explorer_ref,
        'explorer_logical_object_id': intent.explorer_logical_object_id,
        'result': intent.result, 'reason': intent.reason}
    if intent.revealed_card_ref is not None:
        details['revealed_card'] = intent.revealed_card_ref
    host._log(intent.actor, 'explore.complete', f'{intent.explorer_ref} explored for {intent.player}.',
        details, importance=2, changed_objects=changed_objects, changed_players=[intent.player])
    batch = []
    host._dispatch_semantic_event('permanent.explores', details, trigger_batch=batch)
    if intent.explorer_types is not None and 'creature' in intent.explorer_types:
        host._dispatch_semantic_event(CREATURE_EXPLORE_EVENT,
            {**details, 'controller': intent.player, 'card': intent.explorer_ref,
             'types': list(intent.explorer_types)}, trigger_batch=batch)
    enqueue_trigger_batch(host, batch)


__all__ = [
    "capture_explore_source_departure",
    "explore_source_controller",
    "materialize_explore_source_types",
    "explored_object_types",
    "commit_explore_completion",
]
