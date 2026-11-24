"""One land-entry life total from the original artifact's charge counters."""

from collections.abc import Mapping

from ..life_total_setting_model import LifeTotalValueSpec
from ..replacement.immutable import FrozenMap
from .event_counter_threshold import pin_counter_observation_departures


SOURCE_CHARGE_LIFE_CAPABILITY = "life.change.set_total.source_charge_counters"
SOURCE_CHARGE_LIFE_VALUE = "source_charge_life_total"
SOURCE_CHARGE_LIFE_CONTEXT = "source_charge_life_observation"


def source_charge_life_instruction():
    return {
        "op": "set_life_total", "schema_version": 1, "player": "$controller",
        "value": {"kind": SOURCE_CHARGE_LIFE_VALUE, "schema_version": 1},
    }


def valid_source_charge_life_instruction(effect):
    value = effect.get("value")
    return (
        isinstance(value, Mapping)
        and type(value.get("schema_version")) is int
        and type(effect.get("schema_version")) is int
        and FrozenMap(effect) == FrozenMap(source_charge_life_instruction())
    )


def _mentions_source_charge_total(value):
    if isinstance(value, Mapping):
        return value.get("kind") == SOURCE_CHARGE_LIFE_VALUE or any(
            _mentions_source_charge_total(child) for child in value.values()
        )
    return isinstance(value, (list, tuple)) and any(_mentions_source_charge_total(child) for child in value)


def source_charge_life_program_dependencies(program):
    if not _mentions_source_charge_total((program.effects, program.handlers)):
        return ()
    wrapper = program.effects[0] if len(program.effects) == 1 else {}
    effects = wrapper.get("effects")
    if (
        (program.event, program.active_zone) != ("land.enter", "battlefield")
        or program.cost_schema is not None
        or program.target_schema is not None
        or program.handlers
        or program.event_condition != {
            "field": "controller", "op": "eq", "value": "$source.controller",
        }
        or set(wrapper) != {"op", "player", "effects"}
        or wrapper.get("op") != "offer_optional_effect"
        or wrapper.get("player") != "$controller"
        or not isinstance(effects, (list, tuple))
        or len(effects) != 1
        or not isinstance(effects[0], Mapping)
        or not valid_source_charge_life_instruction(effects[0])
    ):
        raise ValueError("A source charge total requires the exact optional controlled land-entry trigger")
    return (SOURCE_CHARGE_LIFE_CAPABILITY, "effect.choice.optional_fixed", "life.change.set_total")


def source_charge_life_trigger_context(source, program):
    if not source_charge_life_program_dependencies(program):
        return {}
    return {SOURCE_CHARGE_LIFE_CONTEXT: {
        "counter": "charge",
        "departure_count": None,
        "subject": {"reference": source.ref, "logical_object_id": source.logical_object_id},
    }}


def _source_charge_observation(context):
    observed = context.get(SOURCE_CHARGE_LIFE_CONTEXT)
    if not isinstance(observed, Mapping) or set(observed) != {"counter", "departure_count", "subject"}:
        raise ValueError("Source-charge life total is missing its sealed source")
    subject, departed = observed["subject"], observed["departure_count"]
    if (
        observed["counter"] != "charge"
        or not isinstance(subject, Mapping)
        or set(subject) != {"reference", "logical_object_id"}
        or any(type(value) is not str or not value for value in subject.values())
        or departed is not None and (type(departed) is not int or departed < 0)
    ):
        raise ValueError("Source-charge life total has malformed public source facts")
    return observed, subject


def resolve_source_charge_life_value(host, value, item):
    if (
        set(value) != {"kind", "schema_version"}
        or value["kind"] != SOURCE_CHARGE_LIFE_VALUE
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
    ):
        raise ValueError("Source-charge life total value is malformed")
    observed, subject = _source_charge_observation(item.context)
    object_id, _, _ = subject["logical_object_id"].rpartition("@")
    if not object_id or item.source_object_id != object_id:
        raise ValueError("Source-charge life total changed its source object")
    card = host.state.cards.get(object_id)
    count = observed["departure_count"]
    if (card is not None and card.zone == "battlefield" and card.ref == subject["reference"]
            and card.logical_object_id == subject["logical_object_id"]):
        count = card.counters.get("charge", 0)
    if type(count) is not int or count < 0:
        raise ValueError("The original source's charge counters are unavailable")
    return LifeTotalValueSpec("fixed", count).to_dict()


def pin_source_charge_life_departures(host, cards, *, error_type=ValueError):
    pin_counter_observation_departures(
        host, cards, context_field=SOURCE_CHARGE_LIFE_CONTEXT,
        read_observation=_source_charge_observation, error_type=error_type,
    )
