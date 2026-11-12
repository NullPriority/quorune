from __future__ import annotations

"""Counter thresholds on an original public event subject at resolution."""

from collections.abc import Mapping

from ..event_counter_reference import EVENT_COUNTER_CONTEXT, EVENT_COUNTER_OBJECT


EVENT_COUNTER_THRESHOLD_OPERATION = "sacrifice_source_if_event_counter_threshold"
EVENT_COUNTER_THRESHOLD_CAPABILITY = "resolution.effect.event_counter_threshold_sacrifice"
EVENT_COUNTER_THRESHOLD_MECHANIC = "fixed-event-counter-threshold-sacrifice"
EVENT_COUNTER_THRESHOLD_COUNT = "$event.counter_threshold_count"
EVENT_COUNTER_THRESHOLD_CONTEXT = "event_counter_threshold"
_SEQUENCE_CAPABILITIES = (
    EVENT_COUNTER_THRESHOLD_CAPABILITY,
    "counter.producer.fixed_effect",
    "counter.producer.fixed_event_object",
    "counter.producer.fixed_event_trigger",
)


def event_counter_threshold_instruction(minimum: int) -> dict:
    return {
        "op": EVENT_COUNTER_THRESHOLD_OPERATION, "schema_version": 1,
        "player": "$controller", "source": "$source.zone_object",
        "counter": "+1/+1", "minimum": minimum, "observed": EVENT_COUNTER_THRESHOLD_COUNT,
    }


def validate_event_counter_threshold(effect, *, resolved=False):
    if (not isinstance(effect, Mapping) or set(effect) != set(event_counter_threshold_instruction(1))
            or effect["op"] != EVENT_COUNTER_THRESHOLD_OPERATION
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
            or effect["counter"] != "+1/+1" or type(effect["minimum"]) is not int or effect["minimum"] < 1):
        raise ValueError("Event counter threshold requires a closed source sacrifice instruction")
    if resolved:
        if (type(effect["player"]) is not str or not effect["player"]
                or effect["source"] is not None and (type(effect["source"]) is not str or not effect["source"])
                or type(effect["observed"]) is not int or effect["observed"] < 0):
            raise ValueError("Event counter threshold requires its resolved controller and count")
    elif effect != event_counter_threshold_instruction(effect["minimum"]):
        raise ValueError("Event counter threshold must read its own event and original source")


def event_counter_threshold_payload_dependencies(value):
    if value.get("op") != EVENT_COUNTER_THRESHOLD_OPERATION:
        return ()
    validate_event_counter_threshold(value)
    return (EVENT_COUNTER_THRESHOLD_CAPABILITY,)


def _validate_counter_sequence(effects):
    if len(effects) != 2:
        raise ValueError("Event counter threshold requires one placement and its ordered sacrifice")
    first, condition = effects
    validate_event_counter_threshold(condition)
    if first != {
        "op": "place_counters", "card": EVENT_COUNTER_OBJECT, "counter": condition["counter"],
        "amount": 1, "source": "$source",
    } or type(first["amount"]) is not int:
        raise ValueError("Event counter threshold requires its original event counter placement")


def event_counter_threshold_node_capabilities(effects, target_schema):
    if target_schema is not None:
        return ()
    try:
        _validate_counter_sequence(effects)
    except (ValueError, TypeError, KeyError):
        return ()
    return _SEQUENCE_CAPABILITIES


def event_counter_threshold_program_dependencies(program):
    def mentions(value):
        if isinstance(value, Mapping):
            return value.get("op") == EVENT_COUNTER_THRESHOLD_OPERATION or any(mentions(child) for child in value.values())
        return isinstance(value, (tuple, list)) and any(mentions(child) for child in value)

    if not mentions(program.effects):
        return ()
    if (program.event != "creature.attacks" or program.active_zone != "battlefield"
            or program.target_schema is not None or program.event_condition != {
                "field": "source_attachment_target_ref", "op": "eq", "value": "$context.card",
            }):
        raise ValueError("Event counter threshold requires one original attached attack subscription")
    _validate_counter_sequence(program.effects)
    return _SEQUENCE_CAPABILITIES


def initial_event_counter_threshold(effects):
    for effect in effects:
        if effect.get("op") == EVENT_COUNTER_THRESHOLD_OPERATION:
            validate_event_counter_threshold(effect)
            return {EVENT_COUNTER_THRESHOLD_CONTEXT: {"counter": effect["counter"], "departure_count": None}}
    return {}


def _threshold_observation(context):
    observed = context.get(EVENT_COUNTER_THRESHOLD_CONTEXT)
    subject = context.get(EVENT_COUNTER_CONTEXT)
    if (not isinstance(observed, Mapping) or set(observed) != {"counter", "departure_count"}
            or observed["counter"] != "+1/+1"
            or observed["departure_count"] is not None and
               (type(observed["departure_count"]) is not int or observed["departure_count"] < 0)
            or not isinstance(subject, Mapping) or set(subject) != {"reference", "logical_object_id"}
            or any(type(value) is not str or not value for value in subject.values())):
        raise ValueError("Event counter threshold lost its sealed public subject")
    return observed, subject


def event_counter_threshold_count(host, item):
    observed, subject = _threshold_observation(item.context)
    object_id = subject["logical_object_id"].rpartition("@")[0]
    card = host.state.cards.get(object_id)
    if (card is not None and card.zone == "battlefield" and card.ref == subject["reference"]
            and card.logical_object_id == subject["logical_object_id"]):
        # Phasing retains these counters and cannot modify them while the
        # permanent is treated as absent.
        return card.counters.get(observed["counter"], 0)
    if observed["departure_count"] is None:
        raise ValueError("Departed event counter subject has no last-known count")
    return observed["departure_count"]


def pin_event_counter_threshold_departures(host, cards, *, error_type=ValueError):
    """Keep only the watched public count of the exact departing incarnation."""
    departing = {(card.ref, card.logical_object_id): card for card in cards if card.zone == "battlefield"}
    if not departing:
        return

    def capture(context):
        if EVENT_COUNTER_THRESHOLD_CONTEXT not in context:
            return None
        observed, subject = _threshold_observation(context)
        source = departing.get((subject["reference"], subject["logical_object_id"]))
        if source is None or observed["departure_count"] is not None:
            return None
        return {**observed, "departure_count": source.counters.get(observed["counter"], 0)}

    try:
        stack_updates = []
        for item in host.state.stack:
            observation = capture(item.context)
            if observation is not None:
                stack_updates.append((item, observation))
        from ..trigger_batches import PendingTriggerBatch
        from ..trigger_processing import replace_pending_trigger_batch

        pending_updates = []
        for index, batch in enumerate(host.state.pending_trigger_batches):
            if not any(EVENT_COUNTER_THRESHOLD_CONTEXT in item.event_facts for item in batch.items):
                continue
            payload = batch.to_dict()
            changed = False
            for group in payload["groups"]:
                for item in group["items"]:
                    observation = capture(item["context"])
                    if observation is not None:
                        item["context"][EVENT_COUNTER_THRESHOLD_CONTEXT] = observation
                        changed = True
            if changed:
                pending_updates.append((index, PendingTriggerBatch.from_dict(payload)))
        for item, observation in stack_updates:
            item.context[EVENT_COUNTER_THRESHOLD_CONTEXT] = observation
        for index, batch in pending_updates:
            replace_pending_trigger_batch(host, index, batch)
    except (KeyError, TypeError, ValueError) as exc:
        raise error_type(str(exc)) from exc
