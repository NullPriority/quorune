from __future__ import annotations

"""Read-only traversal shared by nested capability-shaped effect programs."""

from typing import Any, Mapping


def nested_effect_operations(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        operation = value.get("op")
        if isinstance(operation, str) and operation:
            found.add(operation)
        for child in value.values():
            found.update(nested_effect_operations(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(nested_effect_operations(child))
    return found


def contains_aftermath_kind(value: Any, kind: str) -> bool:
    if isinstance(value, Mapping):
        if value.get("kind") == kind:
            return True
        return any(contains_aftermath_kind(child, kind) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_aftermath_kind(child, kind) for child in value)
    return False


def contains_nested_key(value: Any, key: str) -> bool:
    if isinstance(value, Mapping):
        return key in value or any(contains_nested_key(child, key) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_nested_key(child, key) for child in value)
    return False
