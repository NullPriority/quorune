"""Versioned effective type facts for a committed spell-cast event."""

from collections.abc import Mapping


CAST_EVENT_TYPE_SNAPSHOT_VERSION_FIELD = "cast_event_type_snapshot_version"
CURRENT_CAST_EVENT_TYPE_SNAPSHOT_VERSION = 1


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
