from __future__ import annotations

"""One triggerable announcement for the public results of a single Mill batch."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from ..target_cardinality import selected_target_count_descriptor


MILL_EVENT = "mill.committed_public_nonland"
MILL_COUNT = "committed_nonland_mill_count"
MILL_CAPABILITY = "trigger.event.committed_public_nonland_mill"
MILL_MECHANIC = "trigger-event-committed-public-nonland-mill"
MILL_VARIANT = "committed_public_nonland_mill_counter_targets"
MILL_REQUIREMENTS = (
    MILL_CAPABILITY, "zone.mill.fixed", "zone.mill.committed_public_cards",
    "counter.producer.fixed_event_trigger", "counter.producer.quantified_target_set",
    "counter.producer.fixed_permanent_target_set_effect",
    "target.cardinality.any_number", "target.revalidate_resolution", "trigger.placement.apnap",
)


def committed_mill_trigger_condition():
    return {"field": MILL_COUNT, "op": "truthy", "value": True}


def committed_mill_counter_target_schema(*, maximum=1):
    return {"zones": ["battlefield"], "categories": ["permanent"],
            "types_any": ["creature"], "up_to": maximum}


def committed_mill_counter_effect():
    return {
        "op": "place_counters_on_targets", "schema_version": 2,
        "cards": "$targets", "maximum_targets": selected_target_count_descriptor(),
        "announced_target_count": True, "counter": "+1/+1", "amount": 1, "source": "$source",
    }


def committed_mill_counter_dependencies(effects, target_schema):
    if (len(effects) != 1 or not isinstance(target_schema, Mapping) or type(target_schema.get("up_to")) is not int
            or FrozenMap(target_schema) != FrozenMap(committed_mill_counter_target_schema())
            or type(effects[0].get("schema_version")) is not int or type(effects[0].get("amount")) is not int
            or effects[0].get("announced_target_count") is not True
            or FrozenMap(effects[0]) != FrozenMap(committed_mill_counter_effect())):
        return ()
    return MILL_REQUIREMENTS


def committed_mill_counter_program_dependencies(program):
    if program.event != MILL_EVENT and MILL_CAPABILITY not in program.capability_dependencies:
        return ()
    if (program.event != MILL_EVENT or program.active_zone != "battlefield"
            or program.handlers or program.cost_schema is not None or program.trigger_limit is not None
            or FrozenMap(program.event_condition or {}) != FrozenMap(committed_mill_trigger_condition())
            or MILL_MECHANIC not in program.coverage):
        raise ValueError("Committed nonland Mill counters require their complete normalized battlefield trigger")
    required = committed_mill_counter_dependencies(program.effects, program.target_schema)
    if not required:
        raise ValueError("Committed nonland Mill counters lost their original optional target group")
    return required


def committed_mill_target_context(program, event, occurrence):
    if program.event != MILL_EVENT:
        return {}
    count = occurrence.get(MILL_COUNT)
    required = committed_mill_counter_program_dependencies(program)
    if (event != MILL_EVENT or type(count) is not int or count <= 0
            or not set(required) <= set(program.capability_dependencies)):
        raise ValueError("Committed nonland Mill targets require their positive authorized occurrence count")
    return {"target_schema_override": committed_mill_counter_target_schema(maximum=count)}


def publish_committed_nonland_mill(host, results):
    """Dispatch one positive public count, preserving distinct Mill instructions."""
    from ..milling import current_public_milled_cards
    from ..trigger_processing import enqueue_trigger_batch
    from .event_subscriptions import event_has_capability_subscriber

    if not event_has_capability_subscriber(host, MILL_EVENT, MILL_CAPABILITY):
        return
    count = sum(
        "land" not in host._type_parts(str(host._effective_card_data(card).get("type_line") or ""))[0]
        for result in results for card in current_public_milled_cards(host, result)
    )
    if count == 0:
        return
    triggers = []
    host._dispatch_semantic_event(
        MILL_EVENT, {MILL_COUNT: count, "event_id": f"mill:{host.state.turn_sequence}:{host.state.event_sequence}"},
        sources=host._semantic_event_sources(zones={"battlefield"}), trigger_batch=triggers,
    )
    enqueue_trigger_batch(host, triggers)
