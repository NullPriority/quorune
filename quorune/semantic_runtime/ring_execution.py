"""Canonical Ring state delegation and one completed-action occurrence."""
from __future__ import annotations

from ..ring_model import RING_TEMPTED_EVENT
from ..ring_state import commit_ring_bearer_choice, prepare_ring_temptation
from .ring_intent import BeginRingTemptationIntent, CompleteRingBearerIntent


RING_INTENT_TYPES = (BeginRingTemptationIntent, CompleteRingBearerIntent)


def execute_ring_intent(sink, intent):
    if isinstance(intent, BeginRingTemptationIntent):
        prepare_ring_temptation(sink, player=intent.player, reason=intent.reason)
        return intent.player, None
    if not isinstance(intent, CompleteRingBearerIntent):
        raise TypeError('Unknown typed Ring intent')
    result = commit_ring_bearer_choice(sink, intent.plan, intent.selected_ref)
    chosen = result.chosen_bearer.identity if result.creature_chosen else None
    bearer = result.state.bearer.identity if result.state.bearer is not None else None
    details = {
        'player': intent.player,
        'emblem': result.state.emblem.object_ref,
        'emblem_object_id': result.state.emblem.object_id,
        'emblem_logical_object_id': result.state.emblem.logical_object_id,
        'temptation_count': result.state.temptation_count,
        'creature_chosen': result.creature_chosen,
        'ring_bearer': bearer.object_ref if bearer is not None else None,
        'ring_bearer_object_id': bearer.object_id if bearer is not None else None,
        'ring_bearer_logical_object_id': bearer.logical_object_id if bearer is not None else None,
        'chosen_bearer': chosen.object_ref if chosen is not None else None,
        'chosen_bearer_object_id': chosen.object_id if chosen is not None else None,
        'chosen_bearer_logical_object_id': chosen.logical_object_id if chosen is not None else None,
        'reason': intent.reason,
    }
    # Choice-sensitive and temptation-sensitive observers share this occurrence,
    # with an explicit choice fact, so their waiting triggers batch together.
    sink._dispatch_semantic_event(RING_TEMPTED_EVENT, details)
    return intent.player, None
