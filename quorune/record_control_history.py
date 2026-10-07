from __future__ import annotations

"""Game Record v3 provenance for upkeep-relative control history."""

from typing import Any, Mapping

from .model import CONTROL_HISTORY_VERSION


def serialized_control_history_version(value: int | None) -> int:
    """Return the explicit manifest version for one checkpoint mode."""

    return 0 if value is None else value


def validate_control_history_provenance(
    manifest: Mapping[str, Any],
    state_version: int | None,
) -> None:
    """Bind the additive history marker to initial checkpoint semantics."""

    format_value = manifest.get("format")
    if not isinstance(format_value, Mapping):
        raise ValueError("Record format provenance is malformed")
    declared = format_value.get("control_history_version", 0)
    if type(declared) is not int or declared not in {
        0,
        1,
        CONTROL_HISTORY_VERSION,
    }:
        raise ValueError("Unsupported control-history provenance")
    if declared != serialized_control_history_version(state_version):
        raise ValueError(
            "Control-history provenance does not match the initial checkpoint"
        )


__all__ = [
    "serialized_control_history_version",
    "validate_control_history_provenance",
]
