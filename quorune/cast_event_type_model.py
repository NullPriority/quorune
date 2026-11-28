"""Versioned effective cast and physical card-play facts."""

from collections.abc import Mapping


CAST_EVENT_TYPE_SNAPSHOT_VERSION_FIELD = "cast_event_type_snapshot_version"
CURRENT_CAST_EVENT_TYPE_SNAPSHOT_VERSION = 1
CARD_PLAY_EVENT_FACT_VERSION_FIELD = "card_play_event_fact_version"
CURRENT_CARD_PLAY_EVENT_FACT_VERSION = 1
PHYSICAL_CARD_EVENT_FIELD = "physical_card"

LEGENDARY_SORCERY_FIELD = "legendary_sorcery_rule_version"


def legendary_sorcery_rule_version(value: object) -> int | None:
    if value is not None and (type(value) is not int or value != 1):
        raise ValueError("Unsupported legendary sorcery rule version")
    return value


def read_legendary_sorcery_rule_version(data: Mapping[str, object]) -> int | None:
    if LEGENDARY_SORCERY_FIELD not in data:
        return None
    version = legendary_sorcery_rule_version(data[LEGENDARY_SORCERY_FIELD])
    if version is None:
        raise ValueError("A stored legendary sorcery rule version must be an integer")
    return version


def cast_event_type_snapshot_version(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value != CURRENT_CAST_EVENT_TYPE_SNAPSHOT_VERSION:
        raise ValueError("Unsupported cast event type snapshot version")
    return value


def read_cast_event_type_snapshot_version(data: Mapping[str, object]) -> int | None:
    field = CAST_EVENT_TYPE_SNAPSHOT_VERSION_FIELD
    if field not in data:
        return None
    version = cast_event_type_snapshot_version(data[field])
    if version is None:
        raise ValueError("Cast event type snapshot version cannot be null")
    return version


def cast_event_type_snapshot_fields(version: int | None) -> dict[str, int]:
    version = cast_event_type_snapshot_version(version)
    return {} if version is None else {CAST_EVENT_TYPE_SNAPSHOT_VERSION_FIELD: version}


def cast_event_type_line(
    version: int | None, declared: str, effective: Mapping[str, object],
) -> str:
    if cast_event_type_snapshot_version(version) is None:
        return declared
    line = effective.get("type_line")
    if type(line) is not str or not line.strip():
        raise ValueError("A cast event requires its effective stack type line")
    return line


def card_play_event_fact_version(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value != CURRENT_CARD_PLAY_EVENT_FACT_VERSION:
        raise ValueError("Unsupported physical card-play event fact version")
    return value


def read_card_play_event_fact_version(data: Mapping[str, object]) -> int | None:
    if CARD_PLAY_EVENT_FACT_VERSION_FIELD not in data:
        return None
    version = card_play_event_fact_version(data[CARD_PLAY_EVENT_FACT_VERSION_FIELD])
    if version is None:
        raise ValueError("Physical card-play event fact version cannot be null")
    return version


def card_play_event_fact_state_fields(version: int | None) -> dict[str, int]:
    version = card_play_event_fact_version(version)
    return {} if version is None else {CARD_PLAY_EVENT_FACT_VERSION_FIELD: version}


def physical_card_event_context(state: object, card) -> dict[str, bool]:
    version = card_play_event_fact_version(getattr(state, CARD_PLAY_EVENT_FACT_VERSION_FIELD, None))
    if version is None:
        return {}
    physical_card = card.is_card_object
    if type(physical_card) is not bool:
        raise ValueError("Physical card-play event fact must be a boolean")
    return {PHYSICAL_CARD_EVENT_FIELD: physical_card}
