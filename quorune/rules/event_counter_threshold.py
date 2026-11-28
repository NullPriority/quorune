from __future__ import annotations

"""Counter thresholds on an original public event subject at resolution."""

from collections.abc import Mapping

from ..event_counter_reference import EVENT_COUNTER_CONTEXT, EVENT_COUNTER_OBJECT


EVENT_COUNTER_THRESHOLD_OPERATION = "sacrifice_source_if_event_counter_threshold"
EVENT_COUNTER_THRESHOLD_CAPABILITY = "resolution.effect.event_counter_threshold_sacrifice"
EVENT_COUNTER_THRESHOLD_MECHANIC = "fixed-event-counter-threshold-sacrifice"
EVENT_COUNTER_THRESHOLD_COUNT = "$event.counter_threshold_count"
EVENT_COUNTER_THRESHOLD_CONTEXT = "event_counter_threshold"
SOURCE_DEPLETION_THRESHOLD_VERSION = 2
SOURCE_DEPLETION_THRESHOLD_CAPABILITY = "resolution.effect.cast_counter_source_depletion_threshold"
SOURCE_DEPLETION_COUNTER_COUNT = "$source.depletion_counter_count"
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


def source_depletion_threshold_instruction():
    return {
        **event_counter_threshold_instruction(3),
        "schema_version": SOURCE_DEPLETION_THRESHOLD_VERSION,
        "counter": "depletion",
        "observed": SOURCE_DEPLETION_COUNTER_COUNT,
    }


def source_depletion_counter_effects():
    return (
        {"op": "counter_stack_target", "stack": "$context.stack"},
        {"op": "place_counters", "card": "$source.zone_object", "counter": "depletion",
         "amount": 1, "source": "$source"},
        source_depletion_threshold_instruction(),
    )


def _validate_source_depletion_threshold(effect, *, resolved):
    expected = source_depletion_threshold_instruction()
    if type(effect.get("schema_version")) is not int or type(effect.get("minimum")) is not int:
        raise ValueError("Source depletion threshold requires its exact count and version")
    if resolved:
        if (type(effect.get("player")) is not str or not effect["player"]
                or effect.get("source") is not None and (type(effect["source"]) is not str or not effect["source"])
                or type(effect.get("observed")) is not int or effect["observed"] < 0):
            raise ValueError("Source depletion threshold requires its resolved controller and count")
        expected.update(player=effect["player"], source=effect.get("source"), observed=effect["observed"])
    if effect != expected:
        raise ValueError("Source depletion threshold requires the original source after its exact placement")


def validate_event_counter_threshold(effect, *, resolved=False):
    if isinstance(effect, Mapping) and effect.get("schema_version") == SOURCE_DEPLETION_THRESHOLD_VERSION:
        _validate_source_depletion_threshold(effect, resolved=resolved)
        return
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
    if value["schema_version"] == SOURCE_DEPLETION_THRESHOLD_VERSION:
        return (EVENT_COUNTER_THRESHOLD_CAPABILITY, SOURCE_DEPLETION_THRESHOLD_CAPABILITY)
    return (EVENT_COUNTER_THRESHOLD_CAPABILITY,)


def _validate_counter_sequence(effects):
    if len(effects) != 2:
        raise ValueError("Event counter threshold requires one placement and its ordered sacrifice")
    first, condition = effects
    validate_event_counter_threshold(condition)
    if condition["schema_version"] != 1:
        raise ValueError("Attached event threshold requires its original schema")
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


def source_depletion_counter_node_capabilities(effects, target_schema):
    if target_schema is not None or tuple(effects) != source_depletion_counter_effects():
        return ()
    if type(effects[1].get("amount")) is not int:
        return ()
    try:
        validate_event_counter_threshold(effects[2])
    except (TypeError, ValueError):
        return ()
    return (
        EVENT_COUNTER_THRESHOLD_CAPABILITY, SOURCE_DEPLETION_THRESHOLD_CAPABILITY,
        "stack.counter.cast_event_spell", "stack.counter.effect",
        "counter.producer.fixed_effect", "counter.placement.quantity_replacement",
        "permanent.sacrifice.fixed_source",
    )


def _uses_source_depletion_threshold(program):
    if SOURCE_DEPLETION_THRESHOLD_CAPABILITY in getattr(program, "capability_dependencies", ()):
        return True
    def mentions(value):
        if isinstance(value, Mapping):
            return (
                (value.get("op") == EVENT_COUNTER_THRESHOLD_OPERATION
                 and value.get("schema_version") == SOURCE_DEPLETION_THRESHOLD_VERSION)
                or any(mentions(child) for child in value.values())
            )
        return isinstance(value, (tuple, list)) and any(mentions(child) for child in value)

    return mentions(program.effects)


def source_depletion_counter_program_dependencies(program):
    if not _uses_source_depletion_threshold(program):
        return ()
    deps = source_depletion_counter_node_capabilities(program.effects, program.target_schema)
    if (
        not deps or (program.event, program.active_zone) != ("spell.cast", "battlefield")
        or program.cost_schema is not None or program.handlers
        or program.event_condition != {"field": "controller", "op": "ne", "value": "$source.controller"}
        or "one_or_more_event_batch" in getattr(program, "coverage", ())
    ):
        raise ValueError("Source depletion threshold requires the exact opponent cast and its ordered results")
    return (*deps, "trigger.event.normalized_spell_cast", "counter.producer.fixed_event_trigger", "trigger.placement.apnap")


def event_counter_threshold_program_dependencies(program):
    from .life_gain_counter_reset import life_gain_counter_reset_program_dependencies

    if reset := life_gain_counter_reset_program_dependencies(program):
        return reset
    if source_depletion := source_depletion_counter_program_dependencies(program):
        return source_depletion
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


def initial_event_counter_threshold(effects, *, source=None, current=None):
    from .combat_point_counters import initial_point_counter_observation, uses_point_game_loss

    from .attack_trigger_copy import initial_counter_observation, uses_quest_copy

    for effect in effects:
        if uses_quest_copy(effect):
            return initial_counter_observation(source, current)
        if uses_point_game_loss(effect):
            return initial_point_counter_observation(source, current)
        if effect.get("op") == EVENT_COUNTER_THRESHOLD_OPERATION:
            validate_event_counter_threshold(effect)
            if effect["schema_version"] == SOURCE_DEPLETION_THRESHOLD_VERSION:
                return {}
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


def current_source_depletion_count(host, item):
    """Read the exact original source after the placement instruction."""
    object_id = getattr(item, "source_object_id", None)
    identity = item.context.get("source_logical_object_id")
    if type(object_id) is not str or not object_id or type(identity) is not str or not identity:
        raise ValueError("Source depletion threshold needs its original battlefield identity")
    source = host.state.cards.get(object_id)
    if (source is None or source.zone != "battlefield" or source.phased_out
            or source.logical_object_id != identity):
        return 0
    count = source.counters.get("depletion", 0)
    if type(count) is not int or count < 0:
        raise ValueError("Original depletion counters are malformed")
    return count


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
    from .combat_point_counters import POINT_COUNTER_OBSERVATION, point_counter_observation

    from .attack_trigger_copy import QUEST_CONTEXT, quest_observation

    cards = tuple(cards)
    pin_counter_observation_departures(
        host, cards, context_field=QUEST_CONTEXT, read_observation=quest_observation, error_type=error_type,
    )
    pin_counter_observation_departures(
        host, cards, context_field=POINT_COUNTER_OBSERVATION,
        read_observation=point_counter_observation, error_type=error_type,
    )
    pin_counter_observation_departures(
        host, cards, context_field=EVENT_COUNTER_THRESHOLD_CONTEXT,
        read_observation=_threshold_observation, error_type=error_type,
    )


def pin_counter_observation_departures(host, cards, *, context_field, read_observation, error_type):
    """Retain a named public counter on the exact departing incarnation."""
    departing = {(card.ref, card.logical_object_id): card for card in cards if card.zone == "battlefield"}
    if not departing:
        return

    def capture(context):
        if context_field not in context:
            return None
        observed, subject = read_observation(context)
        source = departing.get((subject["reference"], subject["logical_object_id"]))
        if source is None or observed["departure_count"] is not None:
            return None
        count = source.counters.get(observed["counter"], 0)
        if type(count) is not int or count < 0:
            raise ValueError("A departing public counter observation is malformed")
        return {**observed, "departure_count": count}

    from ..trigger_processing import pin_trigger_context_entry

    try:
        pin_trigger_context_entry(host, context_field, capture)
    except (KeyError, TypeError, ValueError) as exc:
        raise error_type(str(exc)) from exc
