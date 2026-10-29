from __future__ import annotations

"""Additive Game Record v3 state-semantics provenance facade."""

from typing import Any, Mapping

from .record_commander_identity import (
    commander_damage_identity_version,
    validate_commander_damage_identity_provenance,
)
from .record_control_history import (
    serialized_control_history_version,
    validate_control_history_provenance,
)


def format_state_versions(state: Any) -> dict[str, int]:
    """Return canonical manifest markers for additive state semantics."""

    return {
        "commander_damage_identity_version": commander_damage_identity_version(
            state.commander_damage_identity_version
        ),
        "control_history_version": serialized_control_history_version(
            state.control_history_version
        ),
        "departure_history_version": (
            getattr(getattr(state, "turn_history", None), "departure_history_version", None) or 0
        ),
        **({"arrival_history_version":1} if getattr(getattr(state,"turn_history",None),"arrival_history_version",None)==1 else {}),
        **{key:1 for key in ('spell_origin_history_version','graveyard_type_history_version')
            if getattr(getattr(state,'turn_history',None),key,None)==1},
        **({"tap_state_event_version": state.tap_state_event_version} if state.tap_state_event_version is not None else {}),
    }


def validate_state_versions(
    manifest: Mapping[str, Any],
    state: Any,
) -> None:
    """Require manifest and initial-checkpoint semantic versions to agree."""

    validate_commander_damage_identity_provenance(
        manifest, state.commander_damage_identity_version
    )
    validate_control_history_provenance(
        manifest, state.control_history_version
    )
    format_value = manifest.get("format")
    if not isinstance(format_value, Mapping):
        raise ValueError("Record format provenance is malformed")
    declared = format_value.get("departure_history_version", 0)
    current = getattr(getattr(state, "turn_history", None), "departure_history_version", None) or 0
    if type(declared) is not int or declared not in {0, 1} or declared != current:
        raise ValueError("Departure-history provenance does not match the initial checkpoint")
    arrival=format_value.get('arrival_history_version',0)
    actual_arrival=getattr(getattr(state,'turn_history',None),'arrival_history_version',None) or 0
    if type(arrival) is not int or arrival not in {0,1} or arrival!=actual_arrival:raise ValueError('Arrival-history provenance does not match the initial checkpoint')
    for key in ('spell_origin_history_version','graveyard_type_history_version'):
        declared=format_value.get(key,0);actual=getattr(getattr(state,'turn_history',None),key,None) or 0
        if type(declared) is not int or declared not in {0,1} or declared!=actual:raise ValueError('Qualified history provenance does not match the initial checkpoint')
    tap_version = format_value.get("tap_state_event_version", 0)
    if type(tap_version) is not int or tap_version not in {0, 1} or tap_version != (state.tap_state_event_version or 0):
        raise ValueError("Tap-state event provenance does not match the initial checkpoint")


__all__ = ["format_state_versions", "validate_state_versions"]
