"""Canonical serialization of authoritative Game Record checkpoints."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from .model import GameState
from .record_trust import _canonical_hash
from .util import stable_json


RECORD_SCHEMA_VERSION = 3


def capability_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def checkpoint_state(state: GameState) -> dict[str, Any]:
    """Return authoritative state without logs or transport credentials."""
    return copy.deepcopy(state.to_dict(include_ephemeral=False, copy_journal_context=False))


def _normalize_checkpoint_payload(payload: dict[str, Any]) -> None:
    """Normalize a caller-owned top level for replay hashing."""
    payload["events"] = []
    payload["capabilities"] = {}
    # Preserve command-hash compatibility with pre-monarch additive v3
    # checkpoints. Absence and the rules-defined initial ``None`` state are
    # equivalent; an actual designation remains authoritative and hashed.
    if payload.get("monarch") is None:
        payload.pop("monarch", None)


def _hash_checkpoint_payload(payload: dict[str, Any]) -> str:
    _normalize_checkpoint_payload(payload)
    return _canonical_hash(payload)


def authoritative_state_hash(state: GameState | Mapping[str, Any]) -> str:
    # Normalization mutates only the new top-level mapping. Nested values
    # are read by JSON encoding, so hashing does not require an isolated copy.
    payload = (
        state.to_dict(include_ephemeral=False, copy_journal_context=False)
        if isinstance(state, GameState) else dict(state)
    )
    return _hash_checkpoint_payload(payload)


def _checkpoint_metadata(state: GameState) -> dict[str, Any]:
    active_caps = []
    decision = state.pending_decision
    if decision:
        for cap in state.capabilities.values():
            if cap.decision_id == decision.decision_id and not cap.consumed:
                active_caps.append(
                    {
                        "id": capability_id(cap.token),
                        "decision_id": cap.decision_id,
                        "principal": cap.principal,
                        "actor": cap.actor,
                    }
                )
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "kind": "authoritative-checkpoint",
        "active_capabilities": sorted(active_caps, key=lambda item: (item["principal"], item["id"])),
    }


def checkpoint_envelope(state: GameState, *, copy_state: bool = True) -> dict[str, Any]:
    payload = (
        checkpoint_state(state) if copy_state
        else state.to_dict(include_ephemeral=False, copy_journal_context=False)
    )
    return {
        **_checkpoint_metadata(state),
        "state_hash": _hash_checkpoint_payload(dict(payload)),
        "state": payload,
    }


def _encode_record_checkpoint(state: GameState) -> tuple[str, list[bytes]]:
    # The record writer owns the state while encoding it. Reuse canonical member
    # bytes for the wire state and hash, which differ only at the normalized keys.
    payload = state.to_dict(include_ephemeral=False, copy_journal_context=False)
    normalized = dict(payload)
    _normalize_checkpoint_payload(normalized)
    # All metadata keys sort before state, and state_hash sorts after state.
    parts = [stable_json(_checkpoint_metadata(state)).encode("utf-8")[:-1], b',"state":{']
    digest = hashlib.sha256(b"{")
    wire_separator = hash_separator = b""
    for key in sorted(payload.keys() | normalized.keys()):
        member = stable_json(key).encode("utf-8") + b":"
        if key in payload:
            value = stable_json(payload[key]).encode("utf-8")
            parts.extend((wire_separator, member, value))
            wire_separator = b","
        if key in normalized:
            if key not in payload or normalized[key] is not payload[key]:
                value = stable_json(normalized[key]).encode("utf-8")
            digest.update(hash_separator)
            digest.update(member)
            digest.update(value)
            hash_separator = b","
    digest.update(b"}")
    state_hash = digest.hexdigest()
    parts.append(f'}},"state_hash":"{state_hash}"}}'.encode("ascii"))
    return state_hash, parts


def _atomic_checkpoint(path: Path, parts: Iterable[bytes]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.writelines(parts)
    temporary.replace(path)
