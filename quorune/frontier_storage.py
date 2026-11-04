from __future__ import annotations

"""Lossless storage for the generated frontier; expanded semantics stay unchanged."""

from collections.abc import Mapping
from typing import Any


def pack_frontier_document(document: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(document, Mapping) or "frontier_storage_version" in document:
        raise ValueError("Expected an expanded frontier document")
    names: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, list):
            for child in value:
                collect(child)
        elif isinstance(value, Mapping):
            if "runtime_component_refs" in value:
                raise ValueError("Expanded frontier contains reserved component references")
            for key, child in value.items():
                if key == "runtime_component_ids":
                    if not isinstance(child, list) or any(type(item) is not str for item in child):
                        raise ValueError("Frontier component identities must be strings")
                    names.update(child)
                else:
                    collect(child)

    collect(document)
    table = sorted(names)
    positions = {name: index for index, name in enumerate(table)}

    def encode(value: Any) -> Any:
        if isinstance(value, list):
            return [encode(child) for child in value]
        if not isinstance(value, Mapping):
            return value
        return {
            ("runtime_component_refs" if key == "runtime_component_ids" else key):
            ([positions[item] for item in child] if key == "runtime_component_ids" else encode(child))
            for key, child in value.items()
        }

    return {"frontier_storage_version": 1, "runtime_components": table, "document": encode(document)}


def unpack_frontier_document(value: Any) -> Any:
    """Read both historical expanded JSON and the strict storage envelope."""
    if not isinstance(value, Mapping) or "frontier_storage_version" not in value:
        return value
    if (set(value) != {"frontier_storage_version", "runtime_components", "document"}
            or type(value["frontier_storage_version"]) is not int or value["frontier_storage_version"] != 1
            or not isinstance(value["document"], Mapping)
            or "frontier_storage_version" in value["document"]):
        raise ValueError("Invalid frontier storage envelope")
    table = value["runtime_components"]
    if (not isinstance(table, list) or any(type(item) is not str for item in table)
            or table != sorted(set(table))):
        raise ValueError("Invalid frontier component dictionary")
    used: set[int] = set()

    def decode(child: Any) -> Any:
        if isinstance(child, list):
            return [decode(item) for item in child]
        if not isinstance(child, Mapping):
            return child
        if "runtime_component_ids" in child:
            raise ValueError("Packed frontier contains ambiguous expanded component identities")
        result = {}
        for key, item in child.items():
            if key == "runtime_component_refs":
                if (not isinstance(item, list)
                        or any(type(index) is not int or not 0 <= index < len(table) for index in item)):
                    raise ValueError("Invalid frontier component reference")
                used.update(item)
                result["runtime_component_ids"] = [table[index] for index in item]
            else:
                result[key] = decode(item)
        return result

    document = decode(value["document"])
    if used != set(range(len(table))):
        raise ValueError("Frontier component dictionary contains unused identities")
    return document
