"""Version and authority identifiers for the inherent rad ability."""

from collections.abc import Mapping


RAD_COUNTER = "rad"
RADIATION_CAUSE = "radiation"
RAD_VERSION_FIELD = "rad_trigger_version"
RAD_TRIGGER_VERSION = 1
RAD_EFFECT_FIELD = "inherent_rad"
RAD_PRODUCER_FIELD = "rad_producer_version"
RAD_CAPABILITY = "variant.rad.inherent_resolution"
ENTRY_RAD_CHOICE_FIELD = "entry_tapped_rad_choice"
ENTRY_RAD_COUNTER_CHILD = "entry_player_rad_counter"
ENTRY_RAD_CHOICE_HANDLER = "replacement.zone.entry-tapped-rad.v1"
ENTRY_RAD_CHOICE_CAPABILITY = "zone.entry.optional_tapped_rad_counters"
ENTRY_RAD_CHOICE_DEPENDENCIES = (
    ENTRY_RAD_CHOICE_CAPABILITY, "zone.entry.tapped_state", "counter.producer.fixed_player_effect",
    RAD_CAPABILITY, "zone.change.destination_replacement",
)


def entry_rad_choice_descriptor():
    return {
        "handler_id": ENTRY_RAD_CHOICE_HANDLER, "schema_version": 1, "event": "zone.change",
        "source_relation": "affected_object", "subject": {"types_all": ["land"]}, "amount": 2,
    }


def rad_trigger_version(value: object) -> int | None:
    if value is not None and (type(value) is not int or value != RAD_TRIGGER_VERSION):
        raise ValueError("Unsupported inherent rad trigger version")
    return value


def read_rad_trigger_version(data: Mapping) -> int | None:
    if RAD_VERSION_FIELD not in data:
        return None
    version = data[RAD_VERSION_FIELD]
    if version is None:
        raise ValueError("Unsupported inherent rad trigger version")
    return rad_trigger_version(version)


def rad_player_counter_effect(effect: Mapping) -> bool:
    name = effect.get("counter")
    return (
        effect.get("op") == "place_player_counters"
        and isinstance(name, str)
        and " ".join(name.casefold().split()) == RAD_COUNTER
    )


def versioned_rad_player_counter_effect(effect: Mapping) -> bool:
    if RAD_PRODUCER_FIELD not in effect:
        return False
    version = effect[RAD_PRODUCER_FIELD]
    if type(version) is not int or version != RAD_TRIGGER_VERSION or not rad_player_counter_effect(effect):
        raise ValueError("A rad producer needs its exact player-counter descriptor")
    return True
