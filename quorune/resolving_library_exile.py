"""Attribute committed opponent-library exile to an actual resolving effect."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from .continuous_effect_model import ContinuousObjectIdentity
from .opponent_library_exile_model import OPPONENT_LIBRARY_EXILE_EVENT, OpponentLibraryExileOccurrence
from .rules.event_subscriptions import event_has_capability_subscriber


OPPONENT_LIBRARY_EXILE_EVENT_CAPABILITY = "trigger.event.resolving_opponent_library_exile"
_RESOLVING_KINDS = frozenset({"spell", "spell_copy", "activated_ability", "triggered_ability"})


@dataclass(frozen=True, slots=True)
class ResolvingExileSource:
    host: object
    controller: str
    grouped_movements: dict[str, list[ContinuousObjectIdentity]] | None = None


_current_source: ContextVar[ResolvingExileSource | None] = ContextVar("resolving_library_exile_source", default=None)


@contextmanager
def resolving_library_exile_source(host, item, *, group_movements: bool = False):
    """Bound the causal claim to execution of a sealed instruction."""
    source = None
    if (item.kind in _RESOLVING_KINDS and item.context.get("currently_resolving") is True
            and item.controller in host.active_seats and any(active is item for active in host.state.stack)):
        source = ResolvingExileSource(host, item.controller, {} if group_movements else None)
    token = _current_source.set(source)
    try:
        yield
    finally:
        _current_source.reset(token)
    if source is not None and source.grouped_movements:
        _publish_opponent_library_exiles(host, source.controller, source.grouped_movements)


def resolved_library_exile_trigger_batch(host, snapshots, *, deferred=False):
    if deferred:
        return None
    batch = []
    observe_resolved_library_exiles(host, snapshots, trigger_batch=batch)
    return batch


def observe_resolved_library_exiles(host, snapshots, *, trigger_batch=None):
    """Publish one event per opponent library represented in this committed move."""
    source = _current_source.get()
    if source is None or source.host is not host or not event_has_capability_subscriber(
        host, OPPONENT_LIBRARY_EXILE_EVENT, OPPONENT_LIBRARY_EXILE_EVENT_CAPABILITY,
    ):
        return
    grouped = {}
    for card, departure in snapshots:
        if (departure.origin == "library" and card.zone == "exile" and card.object_kind == "card"
                and not card.is_token and card.owner != source.controller and card.owner in host.active_seats):
            grouped.setdefault(card.owner, []).append(ContinuousObjectIdentity(card.object_id, card.logical_object_id))
    if source.grouped_movements is not None:
        for library_owner, objects in grouped.items():
            source.grouped_movements.setdefault(library_owner, []).extend(objects)
        return
    _publish_opponent_library_exiles(host, source.controller, grouped, trigger_batch=trigger_batch)


def _publish_opponent_library_exiles(host, controller, grouped, *, trigger_batch=None):
    for library_owner, objects in grouped.items():
        occurrence = OpponentLibraryExileOccurrence(controller, library_owner, tuple(objects))
        host._dispatch_semantic_event(
            OPPONENT_LIBRARY_EXILE_EVENT, occurrence.event_context(), trigger_batch=trigger_batch,
        )
