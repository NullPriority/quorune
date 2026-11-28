"""Publish an authorized own-library search without exposing its cards."""

from contextlib import contextmanager

from ..replacement.immutable import FrozenMap, thaw_value
from .event_subscriptions import event_has_capability_subscriber


LIBRARY_SEARCHED_EVENT = "library.searched"
OPPONENT_OWN_LIBRARY_SEARCH_CAPABILITY = "trigger.event.opponent_own_library_search"
OPPONENT_OWN_LIBRARY_SEARCH_CONDITION = FrozenMap(
    {"field": "player", "op": "ne", "value": "$source.controller"}
)


def opponent_library_search_counter_draw_effects():
    return (
        {"op": "place_counters", "card": "$source.zone_object", "counter": "+1/+1",
         "amount": 1, "source": "$source"},
        {"op": "draw", "player": "$controller", "count": 1, "private": True},
    )


def opponent_library_search_runtime_dependencies(program):
    if (program.event != LIBRARY_SEARCHED_EVENT
            and OPPONENT_OWN_LIBRARY_SEARCH_CAPABILITY not in program.capability_dependencies):
        return ()
    if ((program.event, program.active_zone) != (LIBRARY_SEARCHED_EVENT, "battlefield")
            or not program.ability_id.startswith("trigger:") or program.target_schema is not None
            or program.cost_schema is not None or program.handlers or program.trigger_limit is not None
            or FrozenMap(program.event_condition or {}) != OPPONENT_OWN_LIBRARY_SEARCH_CONDITION
            or thaw_value(program.effects) != list(opponent_library_search_counter_draw_effects())):
        raise ValueError("An opponent search observer requires its original own-library event and result")
    return (OPPONENT_OWN_LIBRARY_SEARCH_CAPABILITY,)


def _has_observer(host, searcher, owner):
    return searcher == owner and searcher in host.active_seats and event_has_capability_subscriber(
        host, LIBRARY_SEARCHED_EVENT, OPPONENT_OWN_LIBRARY_SEARCH_CAPABILITY,
    )


def dispatch_own_library_search(host, *, searcher, owner):
    if _has_observer(host, searcher, owner):
        host._dispatch_semantic_event(LIBRARY_SEARCHED_EVENT, {"player": searcher})


@contextmanager
def own_library_search_scope(host, *, searcher, owner):
    if not _has_observer(host, searcher, owner):
        yield
        return
    # Snapshot observers before changing the searched cards. A deferred
    # replacement choice rolls back discovery along with the attempted move.
    with host.transaction():
        host._dispatch_semantic_event(LIBRARY_SEARCHED_EVENT, {"player": searcher})
        yield
