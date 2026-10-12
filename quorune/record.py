from __future__ import annotations

import copy
import gzip
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .carddb import CardDatabase
from .engine import CommanderEngine
from .model import Event, GameState
from . import record_state_provenance
from .record_decks import deck_fingerprints, deck_list_fingerprints
from .record_trust import (
    card_program_trust_provenance,
    implicit_semantic_execution_provenance,
    rebase_command_semantics_provenance,
    runtime_trust_provenance,
    verified_replay_inputs,
    validate_manifest_runtime_provenance,
    validate_programs_used_provenance,
)
from .semantics import SemanticRegistry
from .rules.evidence import _capability_validation_snapshot
from .util import stable_json
from .version import __version__

RECORD_SCHEMA_VERSION = 3
ENGINE_VERSION = __version__
TRACE_LEVELS = {"minimal", "standard", "debug"}
RUN_STATES = {
    "created",
    "in_progress",
    "paused",
    "complete",
    "aborted",
    "corrupt",
}

_STANDARD_OMIT = {
    "decision.response",
    "priority.pass",
    "step.begin",
    "turn.cleanup",
    "mana.empty",
    "permanent.untap",
}
_MINIMAL_OMIT = _STANDARD_OMIT | {
    "card.draw",
    "card.draw.private",
    "draw.skip",
    "library.shuffle",
    "mana.produce",
    "mana.ability",
    "zone.move",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def capability_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _canonical_hash(value: Any) -> str:
    compact = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(compact.encode("utf-8")).hexdigest()


def checkpoint_state(state: GameState) -> dict[str, Any]:
    """Return authoritative state without logs or transport credentials."""
    payload = copy.deepcopy(state.to_dict())
    payload["events"] = []
    payload["capabilities"] = {}
    return payload


def authoritative_state_hash(state: GameState | Mapping[str, Any]) -> str:
    payload = checkpoint_state(state) if isinstance(state, GameState) else copy.deepcopy(dict(state))
    payload["events"] = []
    payload["capabilities"] = {}
    # Preserve command-hash compatibility with pre-monarch additive v3
    # checkpoints. Absence and the rules-defined initial ``None`` state are
    # equivalent; an actual designation remains authoritative and hashed.
    if payload.get("monarch") is None:
        payload.pop("monarch", None)
    return _canonical_hash(payload)


def checkpoint_envelope(state: GameState) -> dict[str, Any]:
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
        "state_hash": authoritative_state_hash(state),
        "active_capabilities": sorted(active_caps, key=lambda item: (item["principal"], item["id"])),
        "state": checkpoint_state(state),
    }


def semantics_fingerprint(registry: SemanticRegistry) -> str:
    programs = {
        key: registry.get(key).to_dict()
        for key in registry.keys()
        if registry.get(key) is not None
    }
    return _canonical_hash({"schema_version": 1, "programs": programs})


def database_fingerprint(card_db: CardDatabase) -> dict[str, Any]:
    metadata = card_db.metadata()
    stable_metadata = {
        key: metadata[key]
        for key in sorted(metadata)
        if key not in {"database_path"}
    }
    return {
        "algorithm": "sha256",
        "metadata_hash": _canonical_hash(stable_metadata),
        "metadata": stable_metadata,
    }


def event_for_trace(event: Event, trace_level: str) -> dict[str, Any] | None:
    if trace_level not in TRACE_LEVELS:
        raise ValueError(f"Unknown trace level {trace_level!r}")
    if trace_level == "standard" and event.code in _STANDARD_OMIT:
        return None
    if trace_level == "minimal":
        if event.code in _MINIMAL_OMIT or event.importance <= 0:
            return None
        if event.code in {"combat.attack", "combat.damage"} and not event.details:
            return None
    payload = {
        "id": event.event_id,
        "revision": event.revision,
        "turn": event.turn_sequence,
        "active_player": event.active_player,
        "phase": event.phase,
        "step": event.step,
        "actor": event.actor,
        "code": event.code,
        "details": copy.deepcopy(event.details),
        "visibility": list(event.visibility),
        "importance": event.importance,
        "changed_objects": list(event.changed_objects),
        "changed_players": list(event.changed_players),
    }
    if trace_level == "debug" or not event.details:
        payload["summary"] = event.summary
    return payload


def event_from_record(data: Mapping[str, Any]) -> Event:
    return Event(
        event_id=int(data["id"]),
        revision=int(data.get("revision", 0)),
        turn_sequence=int(data.get("turn", 0)),
        active_player=data.get("active_player"),
        phase=str(data.get("phase") or ""),
        step=str(data.get("step") or ""),
        actor=data.get("actor"),
        code=str(data["code"]),
        summary=str(data.get("summary") or data["code"]),
        details=copy.deepcopy(dict(data.get("details") or {})),
        visibility=list(data.get("visibility") or []),
        importance=int(data.get("importance", 1)),
        changed_objects=list(data.get("changed_objects") or []),
        changed_players=list(data.get("changed_players") or []),
    )


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(stable_json(payload), encoding="utf-8")
    temporary.replace(path)


def _atomic_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    text = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        for row in rows
    )
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def provider_telemetry(
    decisions: Sequence[Mapping[str, Any]],
    commands: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    provider_rows = [
        row for row in decisions if row.get("provider_invoked") is True
    ]
    attempt_indexes: dict[tuple[str, str, str], int] = {}
    retry_calls = 0
    for row in provider_rows:
        key = (
            str(row.get("role") or ""),
            str(row.get("principal") or ""),
            str(row.get("decision_id") or ""),
        )
        index = attempt_indexes.get(key, 0)
        if index > 0 or int(row.get("retry_count") or 0) > 0:
            retry_calls += 1
        attempt_indexes[key] = index + 1
    decision_ids = {
        str(row.get("decision_id"))
        for row in decisions
        if row.get("decision_id")
    }
    pilot_handles = {
        str(row.get("thread_handle") or row.get("thread_id"))
        for row in provider_rows
        if row.get("role") == "pilot"
        and (row.get("thread_handle") or row.get("thread_id"))
    }
    return {
        "game_decisions_created": len(decision_ids),
        "provider_calls_attempted": len(provider_rows),
        "provider_calls_accepted": sum(
            bool(row.get("accepted")) for row in provider_rows
        ),
        "provider_calls_rejected": sum(
            row.get("accepted") is False for row in provider_rows
        ),
        "retry_provider_calls": retry_calls,
        "accepted_commands": len(commands),
        "automatic_decisions": sum(
            row.get("execution") == "planned_automatic"
            for row in commands
        ),
        "arbiter_calls_attempted": sum(
            row.get("role") == "arbiter" for row in provider_rows
        ),
        "unique_pilot_threads": len(pilot_handles),
        "persistent_threads_reused": None,
        "ordered_plans_submitted": sum(
            bool(row.get("accepted"))
            and isinstance(row.get("plan"), list)
            and len(row.get("plan") or []) > 1
            for row in decisions
        ),
        "ordered_plan_actions_executed": sum(
            row.get("execution") == "planned_automatic"
            for row in commands
        ),
    }


def hidden_information_audit(
    decisions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Audit durable pilot inputs for direct cross-seat private-zone exposure."""

    findings: list[dict[str, Any]] = []
    forbidden_keys = {
        "authoritative_state",
        "checkpoint",
        "initial_checkpoint",
        "library_order",
        "analyst",
    }

    def inspect(
        value: Any,
        *,
        seat: str,
        decision_id: str,
        path: tuple[str, ...] = (),
    ) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_text = str(key)
                child_path = (*path, key_text)
                if key_text.lower() in forbidden_keys:
                    findings.append(
                        {
                            "decision_id": decision_id,
                            "seat": seat,
                            "path": ".".join(child_path),
                            "reason": "forbidden authoritative or analyst field",
                        }
                    )
                inspect(
                    child,
                    seat=seat,
                    decision_id=decision_id,
                    path=child_path,
                )
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                inspect(
                    child,
                    seat=seat,
                    decision_id=decision_id,
                    path=(*path, str(index)),
                )
            return
        if (
            isinstance(value, str)
            and path
            and path[-1] == "id"
            and any(
                private_key in path
                for private_key in ("hand", "search_cards", "candidates")
            )
            and value
            and not value.startswith(seat)
        ):
            findings.append(
                {
                    "decision_id": decision_id,
                    "seat": seat,
                    "path": ".".join(path),
                    "reason": f"private candidate belongs to another seat: {value}",
                }
            )

    audited = 0
    for row in decisions:
        if row.get("role") != "pilot":
            continue
        seat = str(row.get("seat") or "")
        context = row.get("decision_context")
        if not seat or not isinstance(context, Mapping):
            continue
        audited += 1
        inspect(
            context,
            seat=seat,
            decision_id=str(row.get("decision_id") or ""),
        )
    return {
        "schema_version": 1,
        "source": "durable_pilot_decision_contexts",
        "pilot_rows_audited": audited,
        "seat_projection_verified": not findings,
        "findings": findings,
    }


def derive_codex_arena_metadata(
    decisions: Sequence[Mapping[str, Any]],
    commands: Sequence[Mapping[str, Any]],
    seats: Sequence[str],
    *,
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    prior = dict(existing or {})
    threads: list[dict[str, Any]] = []
    for seat in seats:
        rows = [
            row
            for row in decisions
            if row.get("principal") == f"pilot:{seat}"
            and row.get("provider_invoked") is True
        ]
        handles = {
            str(row.get("thread_handle") or row.get("thread_id"))
            for row in rows
            if row.get("thread_handle") or row.get("thread_id")
        }
        labels = {
            str(row.get("thread_label"))
            for row in rows
            if row.get("thread_label")
        }
        providers = {
            str(row.get("provider"))
            for row in rows
            if row.get("provider")
        }
        models = {
            str(row.get("model"))
            for row in rows
            if row.get("model")
        }
        configured_models = {
            str(row.get("model_configured"))
            for row in rows
            if row.get("model_configured")
        }
        reasoning_reported = {
            str(row.get("reasoning_effort"))
            for row in rows
            if row.get("reasoning_effort")
        }
        reasoning_configured = {
            str(row.get("reasoning_effort_configured"))
            for row in rows
            if row.get("reasoning_effort_configured")
        }
        decision_ids = {
            str(row.get("decision_id"))
            for row in rows
            if row.get("decision_id")
        }
        retry_calls = 0
        per_decision: Counter[str] = Counter()
        for row in rows:
            key = str(row.get("decision_id") or "")
            if per_decision[key] or int(row.get("retry_count") or 0) > 0:
                retry_calls += 1
            per_decision[key] += 1
        timestamps = sorted(
            str(row.get("invoked_at"))
            for row in rows
            if row.get("invoked_at")
        )
        prior_thread = next(
            (
                item
                for item in prior.get("threads", [])
                if str(item.get("seat")) == seat
            ),
            {},
        )
        reused = len(decision_ids) > 1 or (
            not decision_ids and len(rows) > 1
        )
        threads.append(
            {
                "seat": seat,
                "thread_label": (
                    next(iter(labels))
                    if len(labels) == 1
                    else prior_thread.get("thread_label")
                    or f"quorune-pilot-{seat.lower()}"
                ),
                "thread_handle": (
                    next(iter(handles)) if len(handles) == 1 else None
                ),
                "provider": (
                    next(iter(providers))
                    if len(providers) == 1
                    else "unavailable"
                ),
                "provider_recorded": bool(rows and len(providers) == 1),
                "model_configured": (
                    next(iter(configured_models))
                    if len(configured_models) == 1
                    else None
                ),
                "model_reported": (
                    next(iter(models)) if len(models) == 1 else None
                ),
                "reasoning_effort_configured": (
                    next(iter(reasoning_configured))
                    if len(reasoning_configured) == 1
                    else None
                ),
                "reasoning_effort_reported": (
                    next(iter(reasoning_reported))
                    if len(reasoning_reported) == 1
                    else None
                ),
                "first_provider_call_at": (
                    timestamps[0] if timestamps else None
                ),
                "last_provider_call_at": (
                    timestamps[-1] if timestamps else None
                ),
                "total_calls": len(rows),
                "accepted_calls": sum(
                    bool(row.get("accepted")) for row in rows
                ),
                "rejected_calls": sum(
                    row.get("accepted") is False for row in rows
                ),
                "retry_calls": retry_calls,
                "decisions_served": len(decision_ids),
                "reused": reused,
                "interruption_events": int(
                    prior_thread.get("interruption_events", 0)
                ),
                "restart_events": int(
                    prior_thread.get("restart_events", 0)
                ),
                "provider_identity_verified": bool(rows)
                and all(
                    bool(row.get("provider_identity_verified"))
                    for row in rows
                ),
                "model_identity_verified": bool(rows)
                and all(
                    bool(row.get("model_identity_verified"))
                    for row in rows
                ),
            }
        )
    active = [row for row in threads if row["total_calls"] > 0]
    all_independent = (
        len(active) == len(seats)
        and len(
            {
                row["thread_handle"]
                for row in active
                if row["thread_handle"]
            }
        )
        == len(seats)
    )
    codex_recorded = bool(active) and len(active) == len(seats) and all(
        row["provider"] == "codex_subagent" for row in active
    )
    persistent_reuse = bool(active) and all(
        row["reused"] for row in active
    )
    telemetry = provider_telemetry(decisions, commands)
    telemetry["persistent_threads_reused"] = persistent_reuse
    return {
        "parent_session_id": prior.get("parent_session_id"),
        "pilot_thread_count": len(active),
        "unique_pilot_threads": telemetry["unique_pilot_threads"],
        "persistent_thread_reuse": persistent_reuse,
        "persistent_threads_reused": persistent_reuse,
        "primary_made_strategic_decision": bool(
            prior.get(
                "primary_made_strategic_decision",
                prior.get("parent_made_strategic_decision", False),
            )
        ),
        "parent_made_strategic_decision": bool(
            prior.get(
                "parent_made_strategic_decision",
                prior.get("primary_made_strategic_decision", False),
            )
        ),
        "provider_recorded": bool(active)
        and all(row["provider_recorded"] for row in active),
        "provider_identity_verified": bool(active)
        and all(row["provider_identity_verified"] for row in active),
        "model_configured": sorted(
            {
                row["model_configured"]
                for row in active
                if row["model_configured"]
            }
        ),
        "model_reported": sorted(
            {
                row["model_reported"]
                for row in active
                if row["model_reported"]
            }
        ),
        "model_identity_verified": bool(active)
        and all(row["model_identity_verified"] for row in active),
        "reasoning_effort_configured": sorted(
            {
                row["reasoning_effort_configured"]
                for row in active
                if row["reasoning_effort_configured"]
            }
        ),
        "reasoning_effort_reported": sorted(
            {
                row["reasoning_effort_reported"]
                for row in active
                if row["reasoning_effort_reported"]
            }
        ),
        "seat_projection_verified": bool(
            prior.get("seat_projection_verified", True)
        ),
        "all_active_seats_independently_piloted": all_independent,
        "codex_subagent_run_recorded": codex_recorded,
        "codex_subagent_run": codex_recorded,
        "threads": threads,
        "nested_pilot_subagents": bool(
            prior.get("nested_pilot_subagents", False)
        ),
        "telemetry": telemetry,
        "stop_reason": prior.get("stop_reason"),
    }


def pause_reason_for_state(state: GameState) -> dict[str, Any] | None:
    decision = state.pending_decision
    if decision is None:
        semantic_pause = next(
            (
                annotation
                for annotation in reversed(state.annotations)
                if annotation.get("kind") == "semantic_unsupported"
                and annotation.get("active", True)
            ),
            None,
        )
        if semantic_pause is None:
            return None
        return {
            "kind": "semantic_unsupported",
            "label": semantic_pause.get("label"),
            "semantic_key": semantic_pause.get("semantic_key"),
            "trust_level": semantic_pause.get("trust_level"),
            "stack": semantic_pause.get("stack"),
            "event": semantic_pause.get("event"),
            "semantic_policy": semantic_pause.get(
                "semantic_policy"
            ),
        }
    actor = decision.actors[0] if decision.actors else None
    context = decision.payload_by_actor.get(actor, {}) if actor else {}
    label = context.get("label")
    if not label and state.stack:
        label = state.stack[-1].label
    return {
        "kind": (
            "arbiter_required"
            if decision.role == "arbiter"
            else "pilot_required"
        ),
        "decision_id": decision.decision_id,
        "decision_kind": decision.kind,
        "principal": (
            decision.role
            if decision.role != "pilot"
            else f"pilot:{actor}"
        ),
        "label": label,
    }


def write_initial_checkpoint(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        handle.write(stable_json(payload))
    temporary.replace(path)


def read_initial_checkpoint(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def build_manifest(
    *,
    state: GameState,
    card_db: CardDatabase,
    semantics: SemanticRegistry,
    created_at: str,
    updated_at: str,
    replay_mode: str,
    deck_provenance: Mapping[str, Mapping[str, Any]] | None = None,
    profile_validation: Mapping[str, Mapping[str, Any]] | None = None,
    codex_arena: Mapping[str, Any] | None = None,
    provider_metrics: Mapping[str, Any] | None = None,
    status: str | None = None,
    pause_reason: Mapping[str, Any] | None = None,
    migrated_from: str | None = None,
) -> dict[str, Any]:
    effective_status = (
        "complete"
        if state.game_over
        else str(status or "in_progress")
    )
    if effective_status not in RUN_STATES:
        raise ValueError(f"Unknown record status {effective_status!r}")
    list_fingerprints = deck_list_fingerprints(state)
    provenance = dict(deck_provenance or {})
    validations = dict(profile_validation or {})
    match_values = [
        validations.get(f"pilot:{seat}", {}).get(
            "profile_fingerprint_match"
        )
        for seat in state.turn_order
    ]
    overall_profile_match: bool | str = (
        all(value is True for value in match_values)
        if any(value is not None for value in match_values)
        else "unavailable"
    )
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "record_schema_version": RECORD_SCHEMA_VERSION,
        "record_type": "mtg-commander-game",
        "game_id": state.game_id,
        "engine_version": ENGINE_VERSION,
        "state_version": state.state_version,
        "protocol_version": 2,
        "format": {
            "name": state.config.format_name,
            "review_profile": state.config.review_profile,
            "profile": state.config.effective_profile(len(state.turn_order)),
            "starting_life": state.config.starting_life,
            "free_mulligans": state.config.effective_free_mulligans(len(state.turn_order)),
            "first_player_draws": state.config.effective_first_player_draws(len(state.turn_order)),
            **record_state_provenance.format_state_versions(state),
        },
        "player_count": len(state.turn_order),
        "turn_order": list(state.turn_order),
        "players": [
            {
                "seat": seat,
                "name": state.players[seat].name,
                "deck": state.deck_names.get(seat, ""),
                "deck_fingerprint": list_fingerprints[seat],
                "deck_list_fingerprint": list_fingerprints[seat],
                "deck_source_fingerprint": provenance.get(seat, {}).get(
                    "deck_source_fingerprint"
                ),
                "deck_source": provenance.get(seat, {}).get("source"),
                "profile_validation": validations.get(f"pilot:{seat}"),
            }
            for seat in state.turn_order
        ],
        "fingerprint_algorithm_version": 1,
        "profile_fingerprint_match": overall_profile_match,
        "seed": state.config.seed,
        "trace_level": state.config.trace_level,
        "semantics_fingerprint": semantics_fingerprint(semantics),
        "semantics_registry": {
            "schema_version": 1,
            "hash": semantics_fingerprint(semantics),
        },
        "card_programs": {
            "schema_version": 2,
            "fingerprints": semantics.card_program_fingerprints(),
            "trust": card_program_trust_provenance(semantics),
        },
        "runtime_trust": runtime_trust_provenance(),
        "scryfall": database_fingerprint(card_db),
        "created_at": created_at,
        "started_at": created_at,
        "updated_at": updated_at,
        "ended_at": (
            updated_at
            if effective_status in {"complete", "aborted"}
            else None
        ),
        "status": effective_status,
        **(
            {"pause_reason": copy.deepcopy(dict(pause_reason))}
            if effective_status == "paused" and pause_reason
            else {}
        ),
        **(
            {"abort_reason": copy.deepcopy(dict(pause_reason))}
            if effective_status == "aborted" and pause_reason
            else {}
        ),
        "winner": state.winner,
        "draw": state.draw,
        "final_state_hash": authoritative_state_hash(state),
        "replay": {
            "mode": replay_mode,
            "verification": "not_run",
            "engine_version": ENGINE_VERSION,
            "semantics_fingerprint": semantics_fingerprint(semantics),
            "card_program_fingerprints": (
                semantics.card_program_fingerprints()
            ),
        },
        "review": {
            "classification": "unreviewed",
            "eligible": False,
        },
        **(
            {"codex_arena": copy.deepcopy(dict(codex_arena))}
            if codex_arena
            else {}
        ),
        **(
            {"provider_telemetry": copy.deepcopy(dict(provider_metrics))}
            if provider_metrics is not None
            else {}
        ),
        **({"migrated_from": migrated_from} if migrated_from else {}),
    }


def write_record(
    directory: str | Path,
    *,
    state: GameState,
    card_db: CardDatabase,
    semantics: SemanticRegistry,
    initial_checkpoint: Mapping[str, Any],
    commands: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    created_at: str,
    replay_mode: str = "command_replay",
    deck_provenance: Mapping[str, Mapping[str, Any]] | None = None,
    profile_validation: Mapping[str, Mapping[str, Any]] | None = None,
    codex_arena: Mapping[str, Any] | None = None,
    status: str | None = None,
    pause_reason: Mapping[str, Any] | None = None,
    migrated_from: str | None = None,
) -> dict[str, Any]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    prior_manifest: dict[str, Any] | None = None
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        prior_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        prior_game_id = prior_manifest.get("game_id")
        if prior_game_id and prior_game_id != state.game_id:
            raise ValueError(
                f"Record directory belongs to game {prior_game_id}, not {state.game_id}"
            )
    updated_at = utc_now()
    derived_arena = derive_codex_arena_metadata(
        decisions,
        commands,
        state.turn_order,
        existing=codex_arena,
    )
    if status == "paused" and pause_reason is not None:
        derived_arena["stop_reason"] = copy.deepcopy(pause_reason)
    metrics = provider_telemetry(decisions, commands)
    metrics["persistent_threads_reused"] = derived_arena[
        "persistent_threads_reused"
    ]
    manifest = build_manifest(
        state=state,
        card_db=card_db,
        semantics=semantics,
        created_at=created_at,
        updated_at=updated_at,
        replay_mode=replay_mode,
        deck_provenance=deck_provenance,
        profile_validation=profile_validation,
        codex_arena=derived_arena,
        provider_metrics=metrics,
        status=status,
        pause_reason=pause_reason,
        migrated_from=migrated_from,
    )
    if prior_manifest:
        if not migrated_from and prior_manifest.get("migrated_from"):
            manifest["migrated_from"] = prior_manifest["migrated_from"]
        if prior_manifest.get("created_at"):
            manifest["created_at"] = prior_manifest["created_at"]
        prior_replay = prior_manifest.get("replay", {})
        if (
            prior_manifest.get("final_state_hash") == manifest["final_state_hash"]
            and prior_replay.get("engine_version") == manifest["engine_version"]
            and prior_replay.get("semantics_fingerprint") == manifest["semantics_fingerprint"]
        ):
            manifest["replay"]["verification"] = prior_replay.get("verification", "not_run")
            for field in (
                "scope",
                "verification_strategy",
                "verified_commands",
                "verified_from_command",
                "base_state_hash",
            ):
                if field in prior_replay:
                    manifest["replay"][field] = copy.deepcopy(
                        prior_replay[field]
                    )
    _atomic_json(directory / "checkpoint.json", checkpoint_envelope(state))
    _atomic_jsonl(directory / "commands.jsonl", commands)
    _atomic_jsonl(
        directory / "events.jsonl",
        (
            row
            for event in state.events
            if (row := event_for_trace(event, state.config.trace_level)) is not None
        ),
    )
    _atomic_jsonl(directory / "decisions.jsonl", decisions)
    _atomic_jsonl(
        directory / "opportunities.jsonl",
        state.action_opportunities,
    )
    optimization_keys = (
        "priority_windows_considered",
        "pass_only_windows_skipped",
        "yield_covered_windows",
        "suppressed_empty_windows",
        "suppressed_meaningful_windows",
        "yields_invalidated_by_phase",
        "yields_invalidated_by_draw",
        "yields_invalidated_by_action_change",
        "yields_invalidated_by_stack",
        "yields_invalidated_by_public_change",
    )
    optimization = {
        key: sum(
            int(
                player.stats.get("decision_optimization", {}).get(key, 0)
            )
            for player in state.players.values()
        )
        for key in optimization_keys
    }
    _atomic_json(
        directory / "call-benchmark.json",
        {
            "schema_version": 1,
            "source": "durable_decision_and_command_journals",
            "provider_telemetry": metrics,
            "decision_optimization": optimization,
        },
    )
    _atomic_json(
        directory / "hidden-information-audit.json",
        hidden_information_audit(decisions),
    )
    initial_path = directory / "initial-checkpoint.json.gz"
    if not initial_path.exists():
        write_initial_checkpoint(initial_path, initial_checkpoint)
    # The manifest is the commit marker for this atomically replaced component
    # set, so write it after every journal and checkpoint.
    _atomic_json(directory / "manifest.json", manifest)
    return manifest


def load_record_state(directory: str | Path) -> GameState:
    directory = Path(directory)
    checkpoint = json.loads((directory / "checkpoint.json").read_text(encoding="utf-8"))
    state = GameState.from_dict(checkpoint["state"])
    state.events = [
        event_from_record(row)
        for row in _read_jsonl(directory / "events.jsonl")
    ]
    return state


@_capability_validation_snapshot()
def replay_record(
    directory: str | Path,
    card_db: CardDatabase,
    *,
    semantics_path: str | Path | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    directory = Path(directory)
    manifest, semantics = verified_replay_inputs(
        directory, semantics_path=semantics_path, record_schema=RECORD_SCHEMA_VERSION,
        engine_version=ENGINE_VERSION, fingerprint_semantics=semantics_fingerprint,
    )
    initial = read_initial_checkpoint(directory / "initial-checkpoint.json.gz")
    mode = str(manifest.get("replay", {}).get("mode") or "command_replay")
    if mode == "legacy_snapshot":
        actual = authoritative_state_hash(initial["state"])
        expected = str(manifest["final_state_hash"])
        ok = actual == expected
        if verify and not ok:
            raise ValueError(f"Legacy snapshot hash mismatch: expected {expected}, got {actual}")
        return {
            "ok": ok,
            "mode": mode,
            "commands": 0,
            "final_state_hash": actual,
            "expected_state_hash": expected,
        }

    state = GameState.from_dict(initial["state"])
    record_state_provenance.validate_state_versions(manifest, state)
    engine = CommanderEngine(card_db, state, semantics)
    engine.permissions.reissue_pending()
    applied = _apply_replay_commands(
        engine,
        _read_jsonl(directory / "commands.jsonl"),
        semantics,
        verify=verify,
        capability_profile=str(
            manifest.get("format", {}).get("review_profile")
            or "commander_review"
        ),
        require_runtime_provenance=manifest.get("runtime_trust") is not None,
    )
    actual = authoritative_state_hash(engine.state)
    expected = str(manifest["final_state_hash"])
    ok = actual == expected
    if verify and not ok:
        raise ValueError(
            f"Final state hash mismatch: expected {expected}, got {actual}"
        )
    return {
        "ok": ok,
        "mode": mode,
        "commands": applied,
        "final_state_hash": actual,
        "expected_state_hash": expected,
    }


def _apply_replay_commands(
    engine: CommanderEngine,
    commands: Iterable[Mapping[str, Any]],
    semantics: SemanticRegistry,
    *,
    verify: bool,
    capability_profile: str,
    require_runtime_provenance: bool,
) -> int:
    applied = 0
    current_registry = semantics_fingerprint(semantics)
    for command in commands:
        command_semantics = command.get("semantics", {})
        recorded_registry = (
            command_semantics.get("registry_hash")
            or command.get("semantics_fingerprint")
        )
        if verify and recorded_registry and recorded_registry != current_registry:
            raise ValueError(
                f"Semantic registry mismatch at command {command.get('sequence')}"
            )
        recorded_programs = command_semantics.get("card_programs_used")
        if verify and recorded_programs is not None:
            if not isinstance(recorded_programs, Mapping):
                raise ValueError(
                    f"Malformed CardProgram provenance at command "
                    f"{command.get('sequence')}"
                )
            semantic_keys = [
                str(value.get("key"))
                for value in command_semantics.get("programs_used", [])
                if isinstance(value, Mapping) and value.get("key")
            ]
            current_programs = (
                semantics.card_program_fingerprints_for_keys(semantic_keys)
            )
            normalized_programs = {
                str(key): str(value)
                for key, value in recorded_programs.items()
            }
            if normalized_programs != current_programs:
                raise ValueError(
                    f"CardProgram fingerprint mismatch at command "
                    f"{command.get('sequence')}"
                )
        if verify:
            validate_programs_used_provenance(
                command_semantics.get("programs_used", []),
                semantics,
                profile=capability_profile,
                require_runtime_provenance=require_runtime_provenance,
                sequence=command.get("sequence"),
                implicit_provenance=lambda key: (
                    implicit_semantic_execution_provenance(engine, key)
                ),
            )
        before = authoritative_state_hash(engine.state)
        if verify and before != command.get("before_state_hash"):
            raise ValueError(
                f"Replay diverged before command {command.get('sequence')}: "
                f"expected {command.get('before_state_hash')}, got {before}"
            )
        principal = str(command["principal"])
        capability = engine.permissions.capability_for(principal)
        if capability is None:
            raise ValueError(f"No replay capability for {principal} at command {command.get('sequence')}")
        result = engine.try_submit(
            token=capability.token,
            principal=principal,
            action=str(command["action"]),
            payload=copy.deepcopy(dict(command.get("payload") or {})),
        )
        if not result.ok:
            raise ValueError(f"Replay command {command.get('sequence')} rejected: {result.summary}")
        after = authoritative_state_hash(engine.state)
        if verify and after != command.get("after_state_hash"):
            raise ValueError(
                f"Replay diverged after command {command.get('sequence')}: "
                f"expected {command.get('after_state_hash')}, got {after}"
            )
        applied += 1
    return applied


def verify_record_suffix(
    directory: str | Path,
    card_db: CardDatabase,
    *,
    baseline_state: Mapping[str, Any],
    baseline_commands: int,
) -> dict[str, Any]:
    """Verify newly appended commands from an already verified checkpoint.

    The caller must capture ``baseline_state`` while the record manifest still
    reports a passing replay for exactly ``baseline_commands`` commands. The
    previous command's recorded after-hash anchors that checkpoint to the
    existing proof chain; every appended command is then checked normally.
    """

    from .review_artifacts import write_review_artifacts
    from .session import CommanderSession

    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("schema_version", 0)) != RECORD_SCHEMA_VERSION:
        raise ValueError("Replay requires a Game Record v3 directory")
    semantics = SemanticRegistry(directory / "semantics.json")
    if manifest.get("engine_version") != ENGINE_VERSION:
        raise ValueError(
            f"Engine version mismatch: record={manifest.get('engine_version')} "
            f"runtime={ENGINE_VERSION}"
        )
    if manifest.get("semantics_fingerprint") != semantics_fingerprint(
        semantics
    ):
        raise ValueError("Semantic registry fingerprint does not match record")
    validate_manifest_runtime_provenance(manifest, semantics)

    commands = list(_read_jsonl(directory / "commands.jsonl"))
    if baseline_commands < 0 or baseline_commands > len(commands):
        raise ValueError("Verified replay baseline command count is invalid")
    state_payload = copy.deepcopy(dict(baseline_state))
    state_payload["capabilities"] = {}
    state = GameState.from_dict(state_payload)
    record_state_provenance.validate_state_versions(manifest, state)
    base_state_hash = authoritative_state_hash(state)
    if baseline_commands:
        recorded_base_hash = commands[baseline_commands - 1].get(
            "after_state_hash"
        )
    else:
        initial = read_initial_checkpoint(
            directory / "initial-checkpoint.json.gz"
        )
        recorded_base_hash = authoritative_state_hash(initial["state"])
    if base_state_hash != recorded_base_hash:
        raise ValueError(
            "Verified replay baseline does not match its command-prefix hash"
        )

    engine = CommanderEngine(card_db, state, semantics)
    engine.permissions.reissue_pending()
    suffix_commands = commands[baseline_commands:]
    applied = _apply_replay_commands(
        engine,
        suffix_commands,
        semantics,
        verify=True,
        capability_profile=str(
            manifest.get("format", {}).get("review_profile")
            or "commander_review"
        ),
        require_runtime_provenance=manifest.get("runtime_trust") is not None,
    )
    actual = authoritative_state_hash(engine.state)
    expected = str(manifest["final_state_hash"])
    if actual != expected:
        raise ValueError(
            f"Final state hash mismatch: expected {expected}, got {actual}"
        )
    result = {
        "ok": True,
        "mode": "command_replay",
        "commands": len(commands),
        "suffix_commands": applied,
        "verified_from_command": baseline_commands,
        "verification_strategy": "verified_prefix_suffix",
        "base_state_hash": base_state_hash,
        "final_state_hash": actual,
        "expected_state_hash": expected,
    }

    manifest["replay"].update(
        {
            "verification": "pass",
            "scope": (
                "complete_game"
                if manifest.get("status") == "complete"
                else "accepted_command_prefix"
            ),
            "verification_strategy": "verified_prefix_suffix",
            "verified_commands": len(commands),
            "verified_from_command": baseline_commands,
            "base_state_hash": base_state_hash,
        }
    )
    _atomic_json(manifest_path, manifest)
    session = CommanderSession.load(
        card_db,
        directory,
        semantics_path=directory / "semantics.json",
    )
    write_review_artifacts(
        directory,
        session.engine,
        decisions=session.decisions,
        manifest=manifest,
    )
    return result


def _rebase_command_semantics(
    directory: Path,
    registry: SemanticRegistry,
    *,
    capability_profile: str,
) -> None:
    fingerprint = semantics_fingerprint(registry)
    rows = rebase_command_semantics_provenance(
        _read_jsonl(directory / "commands.jsonl"),
        registry,
        registry_fingerprint=fingerprint,
        capability_profile=capability_profile,
    )
    _atomic_jsonl(directory / "commands.jsonl", rows)


def refresh_record(
    directory: str | Path,
    card_db: CardDatabase,
    *,
    status: str | None = None,
    verify_replay: bool = False,
) -> dict[str, Any]:
    """Rebuild every derived field from durable journals and checkpoint state."""

    from .review_artifacts import write_review_artifacts
    from .session import CommanderSession

    directory = Path(directory)
    session = CommanderSession.load(
        card_db,
        directory,
        semantics_path=directory / "semantics.json",
    )
    if status is not None:
        if status not in RUN_STATES:
            raise ValueError(f"Unknown record status {status!r}")
        session.record_status = status
    if session.state.game_over:
        session.record_status = "complete"
        session.pause_reason = None
    elif (
        session.record_status == "paused"
        or status == "paused"
        or (status is None and session.state.pending_decision is not None)
    ):
        session.pause(
            session.pause_reason
            or pause_reason_for_state(session.state)
        )
    session.save(directory)
    replay_result: dict[str, Any] | None = None
    if verify_replay:
        current_registry = semantics_fingerprint(session.engine.semantics)
        registry_drift = False
        for command in _read_jsonl(directory / "commands.jsonl"):
            recorded_registry = (
                command.get("semantics", {}).get("registry_hash")
                or command.get("semantics_fingerprint")
            )
            if (
                recorded_registry
                and recorded_registry != current_registry
            ):
                registry_drift = True
                break
        if not registry_drift:
            replay_result = replay_record(
                directory,
                card_db,
                semantics_path=directory / "semantics.json",
                verify=True,
            )
        else:
            # A semantic-pack refresh may change the registry hash while
            # leaving an accepted command prefix behavior-identical. Rebase
            # only after a full unchecked prefix run reproduces the recorded
            # checkpoint hash.
            replay_result = replay_record(
                directory,
                card_db,
                semantics_path=directory / "semantics.json",
                verify=False,
            )
            if (
                not replay_result["ok"]
                and session.state.pending_decision is not None
                and session.state.pending_decision.role == "arbiter"
                and session.state.stack
            ):
                # Preserve the exact unresolved boundary of a paused older
                # record. A newly shipped built-in pack must not retroactively
                # turn the recorded arbiter checkpoint into a different
                # private choice.
                semantic_key = session.state.stack[-1].semantic_key
                program = session.engine.semantics.get(semantic_key)
                if (
                    program is not None
                    and program.provenance.get("authored_by")
                    == "generic-search-v1"
                ):
                    session.engine.semantics.remove(str(semantic_key))
                    session.save(directory)
                    replay_result = replay_record(
                        directory,
                        card_db,
                        semantics_path=directory / "semantics.json",
                        verify=False,
                    )
            if replay_result["ok"]:
                _rebase_command_semantics(
                    directory,
                    session.engine.semantics,
                    capability_profile=session.state.config.review_profile,
                )
                replay_result = replay_record(
                    directory,
                    card_db,
                    semantics_path=directory / "semantics.json",
                    verify=True,
                )
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["replay"]["verification"] = (
            "pass" if replay_result["ok"] else "fail"
        )
        manifest["replay"]["scope"] = (
            "accepted_command_prefix"
            if not session.state.game_over
            else "complete_game"
        )
        _atomic_json(manifest_path, manifest)
        write_review_artifacts(
            directory,
            session.engine,
            decisions=session.decisions,
            manifest=manifest,
        )
    manifest = json.loads(
        (directory / "manifest.json").read_text(encoding="utf-8")
    )
    return {
        "record": str(directory),
        "status": manifest.get("status"),
        "pause_reason": manifest.get("pause_reason"),
        "provider_telemetry": manifest.get("provider_telemetry"),
        "codex_arena": manifest.get("codex_arena"),
        "replay": manifest.get("replay"),
        "replay_result": replay_result,
    }


def finalize_record(
    directory: str | Path,
    card_db: CardDatabase,
) -> dict[str, Any]:
    directory = Path(directory)
    checkpoint = json.loads(
        (directory / "checkpoint.json").read_text(encoding="utf-8")
    )
    state = GameState.from_dict(checkpoint["state"])
    status = "complete" if state.game_over else "paused"
    return refresh_record(
        directory,
        card_db,
        status=status,
        verify_replay=True,
    )


def verify_record_integrity(
    directory: str | Path,
    card_db: CardDatabase,
    *,
    replay: bool = True,
) -> dict[str, Any]:
    directory = Path(directory)
    manifest = json.loads(
        (directory / "manifest.json").read_text(encoding="utf-8")
    )
    checkpoint = json.loads(
        (directory / "checkpoint.json").read_text(encoding="utf-8")
    )
    decisions = _read_jsonl(directory / "decisions.jsonl")
    commands = _read_jsonl(directory / "commands.jsonl")
    state = GameState.from_dict(checkpoint["state"])
    errors: list[str] = []
    checkpoint_hash = authoritative_state_hash(state)
    if checkpoint.get("state_hash") != checkpoint_hash:
        errors.append("checkpoint state_hash does not match checkpoint state")
    if manifest.get("final_state_hash") != checkpoint_hash:
        errors.append("manifest final_state_hash does not match checkpoint")
    expected_metrics = provider_telemetry(decisions, commands)
    expected_arena = derive_codex_arena_metadata(
        decisions,
        commands,
        state.turn_order,
        existing=manifest.get("codex_arena"),
    )
    expected_metrics["persistent_threads_reused"] = expected_arena[
        "persistent_threads_reused"
    ]
    if manifest.get("provider_telemetry") != expected_metrics:
        errors.append(
            "manifest provider_telemetry disagrees with durable journals"
        )
    actual_arena = dict(manifest.get("codex_arena") or {})
    for key in (
        "codex_subagent_run_recorded",
        "provider_identity_verified",
        "model_identity_verified",
        "unique_pilot_threads",
        "persistent_thread_reuse",
        "persistent_threads_reused",
        "all_active_seats_independently_piloted",
        "threads",
    ):
        if actual_arena.get(key) != expected_arena.get(key):
            errors.append(
                f"manifest codex_arena.{key} disagrees with durable journals"
            )
    if (
        actual_arena.get("persistent_thread_reuse") is True
        and any(
            row.get("reused") is False
            for row in actual_arena.get("threads", [])
            if int(row.get("total_calls", 0)) > 0
        )
    ):
        errors.append(
            "global persistent reuse contradicts a per-thread reused=false"
        )
    replay_result: dict[str, Any] | None = None
    if replay:
        try:
            replay_result = replay_record(
                directory,
                card_db,
                semantics_path=directory / "semantics.json",
                verify=True,
            )
            if not replay_result["ok"]:
                errors.append("accepted command prefix replay failed")
        except Exception as exc:
            errors.append(f"accepted command prefix replay failed: {exc}")
    if manifest.get("status") == "paused":
        expected_pause = pause_reason_for_state(state)
        actual_pause = manifest.get("pause_reason")
        inferred_kinds = {"pilot_required", "arbiter_required"}
        if (
            isinstance(actual_pause, Mapping)
            and actual_pause.get("kind") not in inferred_kinds
        ):
            if not actual_pause.get("kind") or not actual_pause.get("label"):
                errors.append(
                    "explicit paused manifest reason is incomplete"
                )
            if (
                actual_pause.get("decision_id")
                and state.pending_decision
                and actual_pause.get("decision_id")
                != state.pending_decision.decision_id
            ):
                errors.append(
                    "explicit paused manifest decision_id disagrees with checkpoint"
                )
            if actual_arena.get("stop_reason") != actual_pause:
                errors.append(
                    "codex_arena stop_reason disagrees with explicit pause_reason"
                )
        elif actual_pause != expected_pause:
            errors.append(
                "paused manifest pause_reason disagrees with checkpoint"
            )
    return {
        "ok": not errors,
        "record": str(directory),
        "status": manifest.get("status"),
        "errors": errors,
        "replay": replay_result,
        "expected_provider_telemetry": expected_metrics,
    }


def migrate_v2_game(
    game_json: str | Path,
    output: str | Path,
    card_db: CardDatabase,
    *,
    trace_level: str = "standard",
    semantics_path: str | Path | None = None,
) -> dict[str, Any]:
    game_json = Path(game_json)
    state = GameState.load(game_json)
    state.config.trace_level = trace_level
    state.config.profile = state.config.effective_profile(len(state.turn_order))
    semantics = SemanticRegistry(semantics_path)
    created_at = utc_now()
    # Older elimination code moved every owned card to the public ``outside``
    # zone and marked it known to every seat. That did not prove the hidden
    # hand or library had actually been revealed. Reconstruct only identities
    # supported by public event evidence and otherwise preserve the restrictive
    # knowledge state.
    public_refs: set[str] = set()
    public_codes = {
        "land.play",
        "stack.cast",
        "stack.activate",
        "combat.attack",
        "combat.block",
        "library.search",
        "cleanup.discard",
        "zone.move",
        "permanent.untap",
        "state.creatures_died",
    }
    for event in state.events:
        if event.code not in public_codes:
            continue
        details = event.details
        for key in ("object", "source", "card", "kept"):
            if details.get(key):
                public_refs.add(str(details[key]))
        for key in ("objects", "moved"):
            public_refs.update(str(value) for value in details.get(key) or [])
    restricted = 0
    for card in state.cards.values():
        if (
            card.owner in state.eliminated_players
            and card.zone == "outside"
            and card.ref not in public_refs
        ):
            card.known_to = [card.owner]
            card.revealed_to = []
            card.annotations["migration_hidden_zone_uncertain"] = True
            card.annotations["hidden_after_owner_left"] = True
            restricted += 1
    if restricted:
        state.annotations.append(
            {
                "kind": "migration_uncertainty",
                "scope": "eliminated-player hidden zones",
                "objects_restricted": restricted,
                "note": (
                    "V2 did not retain enough history to reconstruct exact "
                    "knowledge; identities were kept private unless public "
                    "event evidence supported disclosure."
                ),
            }
        )
    initial = checkpoint_envelope(state)
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    existing_manifest = output_path / "manifest.json"
    if existing_manifest.exists():
        existing_game = json.loads(existing_manifest.read_text(encoding="utf-8")).get("game_id")
        if existing_game and existing_game != state.game_id:
            raise ValueError(
                f"Migration output belongs to game {existing_game}, not {state.game_id}"
            )
    write_initial_checkpoint(output_path / "initial-checkpoint.json.gz", initial)
    decisions = []
    for event in state.events:
        if event.code != "decision.response":
            continue
        decisions.append(
            {
                "sequence": len(decisions) + 1,
                "decision_id": event.details.get("decision"),
                "kind": None,
                "role": "pilot" if event.actor in state.players else event.actor,
                "principal": (
                    f"pilot:{event.actor}" if event.actor in state.players else event.actor
                ),
                "actor": event.actor,
                "seat": event.actor if event.actor in state.players else None,
                "action": event.details.get("action"),
                "accepted": True,
                "legacy_incomplete": True,
                "legal_alternatives": "unavailable",
                "reason": "unavailable in v2 record",
                "plan": "unavailable in v2 record",
                "plan_category": None,
                "provider_invoked": None,
                "retry_count": 0,
                "phase": event.phase,
                "step": event.step,
                "projected_state_hash": None,
                "observation_revision": event.revision,
                "observation_base_hash": None,
                "turn": event.turn_sequence,
            }
        )
    manifest = write_record(
        output,
        state=state,
        card_db=card_db,
        semantics=semantics,
        initial_checkpoint=initial,
        commands=[],
        decisions=decisions,
        created_at=created_at,
        replay_mode="legacy_snapshot",
        migrated_from=str(game_json),
    )
    manifest["replay"]["verification"] = "snapshot_only"
    manifest["started_at"] = None
    manifest["ended_at"] = None
    manifest["migrated_at"] = created_at
    _atomic_json(output_path / "manifest.json", manifest)
    return manifest


def inspect_game(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if path.is_dir() and (path / "manifest.json").exists():
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        checkpoint = json.loads((path / "checkpoint.json").read_text(encoding="utf-8"))
        return {
            "record_version": int(manifest.get("schema_version", 0)),
            "kind": "game-record",
            "path": str(path),
            "game_id": manifest.get("game_id"),
            "status": manifest.get("status"),
            "profile": manifest.get("format", {}).get("profile"),
            "trace_level": manifest.get("trace_level"),
            "commands": len(_read_jsonl(path / "commands.jsonl")),
            "events": len(_read_jsonl(path / "events.jsonl")),
            "decisions": len(_read_jsonl(path / "decisions.jsonl")),
            "state_hash": checkpoint.get("state_hash"),
            "replay": manifest.get("replay"),
        }
    game_json = path / "game.json" if path.is_dir() else path
    state = GameState.load(game_json)
    counts = Counter(event.code for event in state.events)
    by_ref = {card.ref: card for card in state.cards.values()}
    spells: dict[str, list[str]] = {seat: [] for seat in state.turn_order}
    cleanup_discards = Counter()
    for event in state.events:
        if event.code == "stack.cast" and event.actor in spells:
            ref = str(event.details.get("object") or "")
            spells[event.actor].append(
                by_ref[ref].printed_name if ref in by_ref else ref
            )
        elif event.code == "cleanup.discard" and event.actor:
            cleanup_discards[event.actor] += len(event.details.get("objects") or [])
    return {
        "record_version": 2,
        "kind": "legacy-monolith",
        "path": str(game_json),
        "game_id": state.game_id,
        "status": "complete" if state.game_over else "in_progress",
        "profile": state.config.effective_profile(len(state.turn_order)),
        "bytes": game_json.stat().st_size,
        "events": len(state.events),
        "capabilities": len(state.capabilities),
        "event_breakdown": {
            "decisions": counts["decision.response"],
            "priority_passes": counts["priority.pass"],
            "step_boundaries": counts["step.begin"],
            "lands_played": counts["land.play"],
            "spells_cast": counts["stack.cast"],
            "abilities_activated": counts["stack.activate"],
        },
        "players": {
            seat: {
                "deck": state.deck_names.get(seat, ""),
                "turns_begun": state.players[seat].turns_begun,
                "spells_cast": spells[seat],
                "cleanup_discards": cleanup_discards[seat],
            }
            for seat in state.turn_order
        },
        "winner": state.winner,
        "eliminated_players": list(state.eliminated_players),
        "warning": "Legacy game.json has no replayable command payloads or complete decision audit.",
    }
