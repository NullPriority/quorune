from __future__ import annotations

"""Read-only traversal shared by nested capability-shaped effect programs."""

from typing import Any, Mapping


def nested_effects(value: Any):
    if isinstance(value, Mapping):
        operation = value.get("op")
        if isinstance(operation, str) and operation:
            yield value
        for child in value.values():
            yield from nested_effects(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from nested_effects(child)


def nested_effect_operations(value: Any) -> set[str]:
    return {effect["op"] for effect in nested_effects(value)}


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
