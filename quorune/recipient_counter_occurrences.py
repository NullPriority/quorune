"""Grouped committed counters on one physical or player recipient."""

from collections.abc import Mapping
from dataclasses import dataclass

from .counter_names import normalized_counter_name
from .counter_placement_event_model import CounterPlacementOccurrence
from .replacement.immutable import FrozenMap


RECIPIENT_COUNTER_EVENT = "counter.recipient_put"


@dataclass(frozen=True, slots=True)
class RecipientCounterOccurrence(CounterPlacementOccurrence):
    def __post_init__(self):
        context = FrozenMap(self.context)
        object.__setattr__(self, "context", context)
        kinds = context.get("counter_kinds")
        ids = context.get("counter_event_ids")
        realm = context.get("recipient_kind")
        identity = context.get("recipient_logical_id")
        if (realm not in {"player", "permanent"}
                or any(type(context.get(key)) is not str or not context[key]
                       for key in ("recipient", "placing_player", "reason"))
                or realm == "player" and identity is not None
                or realm == "permanent" and (type(identity) is not str or not identity)
                or not isinstance(kinds, Mapping) or not kinds
                or any(type(key) is not str or normalized_counter_name(key) != key
                       or type(amount) is not int or amount <= 0 for key, amount in kinds.items())
                or type(context.get("amount")) is not int or context["amount"] != sum(kinds.values())
                or not isinstance(ids, tuple) or not ids or len(ids) != len(set(ids))
                or any(type(value) is not str or not value for value in ids)
                or context.get("event_id") != _event_id(ids)):
            raise ValueError("A recipient count requires positive committed counter leaves and their placing player")


def _event_id(ids):
    return RECIPIENT_COUNTER_EVENT + ":" + ":".join(ids)


def recipient_counter_occurrences(host, events, *, reason):
    """Combine all actual counter kinds for each recipient and placing player within one instruction."""
    from .counter_permission_events import apply_counter_permissions

    groups = {}
    for event in events:
        event = apply_counter_permissions(event)
        amount = event.payload["amount"]
        if not amount:
            continue
        actor = event.payload.get("placing_player")
        if type(actor) is not str or actor not in host.active_seats:
            raise ValueError("An actual placement must retain its original placing player")
        if event.affected_object is None:
            recipient = event.affected_player
            if recipient not in host.active_seats or event.payload.get("target_kind") != "player":
                continue
            kind, identity = "player", None
        else:
            card = host.state.cards.get(event.affected_object.object_id)
            if card is None or card.zone != "battlefield":
                continue
            recipient, kind, identity = card.ref, "permanent", card.logical_object_id
            original = event.payload.get("target_logical_object_id")
            if original is not None and original != identity:
                raise ValueError("The counter recipient changed its battlefield incarnation")
        key = kind, recipient, identity, actor
        group = groups.setdefault(key, {"amount": 0, "counter_kinds": {}, "counter_event_ids": []})
        counter = event.payload["counter_name"]
        group["counter_kinds"][counter] = group["counter_kinds"].get(counter, 0) + amount
        group["counter_event_ids"].append(event.event_id)
        group["amount"] += amount

    return tuple(RecipientCounterOccurrence(FrozenMap({
        **group, "recipient_kind": kind, "recipient": ref, "recipient_logical_id": identity,
        "placing_player": placing, "reason": reason, "event_id": _event_id(group["counter_event_ids"]),
    })) for (kind, ref, identity, placing), group in groups.items())
