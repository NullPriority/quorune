from __future__ import annotations

"""Sealed public battlefield occurrences from canonical counter removals."""

from dataclasses import dataclass
from typing import Any, Sequence

from .ability_fragments import ability_fragment_to_dict, canonical_ability_fragments
from .counter_state import CounterTransition
from .replacement.immutable import FrozenMap, thaw_value
from .trigger_processing import enqueue_trigger_batch
from .zone_trigger_events import sealed_public_characteristic_facts


COUNTER_REMOVAL_EVENT = "counter.removed"


@dataclass(frozen=True, slots=True)
class CounterRemovalEventBatch:
    occurrences: tuple[FrozenMap, ...]
    source_identities: tuple[tuple[str, str], ...]
    source_zones: FrozenMap
    source_characteristics: FrozenMap


def capture_counter_removal_events(
    host: Any, transitions: Sequence[CounterTransition],
) -> CounterRemovalEventBatch | None:
    """Read CR 603.10 characteristics after commit and before stabilization."""
    changes = tuple(t for t in transitions if t.subject_kind == "permanent" and t.applied_delta < 0
                    and t.expected_zone == "battlefield")
    if not changes or not callable(getattr(host, "_semantic_event_sources", None)):
        return None
    from .trigger_discovery import _event_programs_for_source, _selected_event_subscription
    selected = []
    characteristics = {}
    zones = {}
    for source in host._semantic_event_sources(zones={"battlefield"}):
        zone, data, programs = _event_programs_for_source(
            host, source, COUNTER_REMOVAL_EVENT, source_zones=None, source_characteristics=None,
        )
        if not any(_selected_event_subscription(p, COUNTER_REMOVAL_EVENT)[0] for p in programs):
            continue
        sealed = dict(data)
        sealed["ability_fragments"] = [ability_fragment_to_dict(fragment)
                                      for fragment in canonical_ability_fragments(data.get("ability_fragments", ()))]
        selected.append((source.object_id, source.logical_object_id))
        characteristics[source.object_id] = sealed
        zones[source.object_id] = zone
    if not selected:
        return None
    occurrences = []
    for transition in changes:
        card = host.state.cards[transition.subject_id]
        data = host._effective_card_data(card)
        occurrences.append(FrozenMap({
            "card": card.ref, "card_object_id": card.object_id,
            "card_object_identity": card.logical_object_id,
            "card_zone_change_counter": card.zone_change_counter,
            "controller": card.controller, "owner": card.owner, "zone": card.zone,
            "counter": transition.counter_name, "amount": -transition.applied_delta,
            "counter_before": transition.before, "counter_after": transition.after,
            "token": card.object_kind == "token", **sealed_public_characteristic_facts(data),
        }))
    return CounterRemovalEventBatch(tuple(occurrences), tuple(selected), FrozenMap(zones), FrozenMap(characteristics))


def dispatch_counter_removal_events(host: Any, batch: CounterRemovalEventBatch | None) -> None:
    if batch is None:
        return
    if not isinstance(batch, CounterRemovalEventBatch):
        raise ValueError("Counter removal dispatch requires a sealed batch")
    sources = []
    for object_id, logical_id in batch.source_identities:
        source = host.state.cards.get(object_id)
        if source is None or source.logical_object_id != logical_id:
            raise ValueError("Counter removal source identity changed during commit")
        sources.append(source)
    triggers = []
    for occurrence in batch.occurrences:
        host._dispatch_semantic_event(
            COUNTER_REMOVAL_EVENT, thaw_value(occurrence), sources=sources,
            source_zones=thaw_value(batch.source_zones),
            source_characteristics=thaw_value(batch.source_characteristics), trigger_batch=triggers,
        )
    enqueue_trigger_batch(host, triggers)
