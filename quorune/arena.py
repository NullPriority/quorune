from __future__ import annotations

import copy
import json
import os
import socket
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, TextIO

from .carddb import CardDatabase
from .pilot import PLAN_CATEGORIES, PilotResponse
from .record import ENGINE_VERSION, utc_now
from .report import derive_review
from .session import CommanderSession
from .semantic_runtime.hand_visibility import hand_card_is_known_to
from .util import stable_json

PILOT_TOOL_NAMES = (
    "get_task",
    "submit_action",
    "get_rules",
    "get_profile",
    "get_memory",
    "update_memory",
)
FORBIDDEN_PILOT_RESPONSE_FIELDS = {
    "principal",
    "seat",
    "actor",
    "cap",
    "capability",
    "provider",
    "model",
    "model_id",
    "reasoning_effort",
    "thread_id",
    "thread_label",
    "parent_session_id",
    "provider_invoked",
    "invocation_id",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "latency_ms",
    "estimated_input_tokens",
    "effects",
    "semantic_key",
}


def _forbidden_response_paths(
    value: Any,
    *,
    path: str = "$",
) -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            if key in FORBIDDEN_PILOT_RESPONSE_FIELDS:
                found.append(child_path)
            found.extend(_forbidden_response_paths(child, path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(
                _forbidden_response_paths(child, path=f"{path}[{index}]")
            )
    return found


def primary_session_prompt(game_dir: str | Path) -> str:
    directory = Path(game_dir).resolve()
    return (
        "Use $quorune-pilot-harness as the neutral coordinator for the Commander "
        f"record at {directory}. Run this primary session with GPT-5.6 Sol "
        "Ultra. Validate all four exact-list profiles, then use "
        "arena-codex-run to start quorune_pilot_a through quorune_pilot_d exactly "
        "once with gpt-5.6-sol/low/priority and route every later seat task "
        "to its original persistent session through the fixed-seat broker. "
        "Confirm semantic_policy is trusted_only before the first pilot call. "
        "Never pilot a seat, never disclose another seat's hidden information, "
        "and stop immediately if suppressed_meaningful_windows becomes nonzero. "
        "For the one-shot pilot-tool fallback, repeat the actual provider, "
        "model, reasoning effort, stable thread ID/label, provider-invoked, "
        "and identity-verification flags on every submit-action invocation; "
        "never shorten that command on later turns. "
        "Continue until turn sequence 8, a win, an unresolved material semantic, "
        "or a fidelity failure; then save, replay-verify, and report this only "
        "as pilot_test when lists are duplicated."
    )


@dataclass(frozen=True, slots=True)
class PilotInvocationIdentity:
    provider: str
    model: str | None = None
    reasoning_effort: str | None = None
    thread_id: str | None = None
    thread_label: str | None = None
    parent_session_id: str | None = None
    provider_invoked: bool = False
    provider_identity_verified: bool = False
    model_identity_verified: bool = False
    model_configured: str | None = None
    reasoning_effort_configured: str | None = None
    invocation_id: str | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    latency_ms: float | None = None

    def audit_fields(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "thread_handle": self.thread_id,
        }


@contextmanager
def _record_lock(directory: Path) -> Iterator[None]:
    """Serialize seat façades and replace stale legacy locks with a PID lease."""

    path = directory / ".arena.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                handle.seek(0)
                raw = handle.read().decode("utf-8", errors="replace").strip()
                recovered = bool(raw and not raw.startswith("{"))
                lease = {
                    "schema_version": 1,
                    "active": True,
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "acquired_at": utc_now(),
                    "expires_unix": time.time() + 120,
                    "recovered_from_stale": recovered,
                }
                handle.seek(0)
                handle.truncate()
                handle.write(stable_json(lease).encode("utf-8"))
                handle.flush()
                yield
            finally:
                lease["active"] = False
                lease["released_at"] = utc_now()
                handle.seek(0)
                handle.truncate()
                handle.write(stable_json(lease).encode("utf-8"))
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                raw = handle.read().decode("utf-8", errors="replace").strip()
                recovered = bool(raw and not raw.startswith("{"))
                lease = {
                    "schema_version": 1,
                    "active": True,
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "acquired_at": utc_now(),
                    "expires_unix": time.time() + 120,
                    "recovered_from_stale": recovered,
                }
                handle.seek(0)
                handle.truncate()
                handle.write(stable_json(lease).encode("utf-8"))
                handle.flush()
                yield
            finally:
                lease["active"] = False
                lease["released_at"] = utc_now()
                handle.seek(0)
                handle.truncate()
                handle.write(stable_json(lease).encode("utf-8"))
                handle.flush()
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class SeatScopedPilotTools:
    """A fixed-seat capability façade; no method accepts a seat or principal."""

    def __init__(
        self,
        session: CommanderSession,
        seat: str,
        *,
        game_dir: str | Path | None = None,
        db_path: str | Path | None = None,
        identity: PilotInvocationIdentity | None = None,
    ):
        if seat not in session.state.players:
            raise ValueError(f"Unknown seat {seat!r}")
        self._session = session
        self._seat = seat
        self._principal = f"pilot:{seat}"
        self._game_dir = Path(game_dir).resolve() if game_dir else None
        self._db_path = Path(db_path).resolve() if db_path else None
        self._identity = identity
        self._profile_cache: dict[str, Any] | None = None

    @property
    def seat(self) -> str:
        return self._seat

    @classmethod
    def open(
        cls,
        *,
        game_dir: str | Path,
        db_path: str | Path,
        seat: str,
        identity: PilotInvocationIdentity | None = None,
    ) -> "SeatScopedPilotTools":
        db = CardDatabase(db_path)
        session = CommanderSession.load(
            db,
            game_dir,
            semantics_path=Path(game_dir) / "semantics.json",
        )
        return cls(
            session,
            seat,
            game_dir=game_dir,
            db_path=db_path,
            identity=identity,
        )

    def tool_names(self) -> tuple[str, ...]:
        return PILOT_TOOL_NAMES

    def close(self) -> None:
        """Release the local card database held by this façade."""

        self._session.card_db.close()

    def _reload(self) -> None:
        if self._game_dir is None or self._db_path is None:
            return
        old_db = self._session.card_db
        db = CardDatabase(self._db_path)
        self._session = CommanderSession.load(
            db,
            self._game_dir,
            semantics_path=self._game_dir / "semantics.json",
        )
        if old_db is not db:
            old_db.close()

    @staticmethod
    def _without_capability(packet: Mapping[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(dict(packet))
        decision = result.get("decision")
        if isinstance(decision, dict):
            decision.pop("cap", None)
        return result

    def get_task(self) -> dict[str, Any] | None:
        if self._game_dir:
            with _record_lock(self._game_dir):
                self._reload()
                command_count = len(self._session.commands)
                plans_before = copy.deepcopy(self._session.plans)
                result = self._get_task_loaded()
                if (
                    len(self._session.commands) != command_count
                    or self._session.plans != plans_before
                ):
                    self._session.save(self._game_dir)
                return result
        return self._get_task_loaded()

    def _get_task_loaded(self) -> dict[str, Any] | None:
        if self._principal not in self._session.pending_principals():
            return None
        if self._session.plans.get(self._principal):
            self._session.next_task(full=True)
            if self._principal not in self._session.pending_principals():
                return None
        return self._without_capability(
            self._session.packet(self._principal, full=True)
        )

    def submit_action(
        self, response: Mapping[str, Any]
    ) -> dict[str, Any]:
        if self._game_dir:
            with _record_lock(self._game_dir):
                self._reload()
                result = self._submit_loaded(response)
                self._session.save(self._game_dir)
                return result
        return self._submit_loaded(response)

    def _submit_loaded(
        self, response: Mapping[str, Any]
    ) -> dict[str, Any]:
        if self._principal not in self._session.pending_principals():
            return {
                "accepted": False,
                "error": f"{self._seat} has no current task",
                "retry": None,
            }
        normalized = copy.deepcopy(dict(response))
        if "yield_mode" in normalized and "yield" not in normalized:
            normalized["yield"] = normalized.pop("yield_mode")
        decision = self._session.state.pending_decision
        if decision and decision.kind != "priority":
            normalized.pop("yield", None)
        forbidden = sorted(set(_forbidden_response_paths(normalized)))
        if forbidden:
            return self._reject_pilot_response(
                normalized,
                "Pilot response contains transport/authority fields: "
                + ", ".join(forbidden),
            )
        identity_error = self._codex_identity_error()
        if identity_error:
            return self._reject_pilot_response(normalized, identity_error)
        if self._identity and self._identity.provider == "codex_subagent":
            missing: list[str] = []
            if not normalized.get("plan"):
                missing.append("plan")
            if not str(normalized.get("reason") or "").strip():
                missing.append("reason")
            if normalized.get("confidence") is None:
                missing.append("confidence")
            if missing:
                return self._reject_pilot_response(
                    normalized,
                    "Codex pilot response is missing required audit fields: "
                    + ", ".join(missing),
                )
        try:
            payload = PilotResponse.from_mapping(normalized).engine_response()
        except (TypeError, ValueError) as exc:
            return self._reject_pilot_response(
                normalized, f"Invalid pilot response: {exc}"
            )
        decision_id = self._session.state.pending_decision.decision_id
        payload["retry_count"] = sum(
            1
            for row in self._session.decisions
            if row.get("principal") == self._principal
            and row.get("decision_id") == decision_id
            and row.get("accepted") is False
        )
        if self._identity is not None:
            payload.update(self._identity.audit_fields())
            payload["invoked_at"] = utc_now()
        result = self._session.act(self._principal, payload)
        return {
            "accepted": result.ok,
            "error": None if result.ok else result.summary,
            "event_ids": list(result.event_ids),
            "retry": None if result.ok else self._get_task_loaded(),
        }

    def _codex_identity_error(self) -> str | None:
        """Reject unauditable or drifting Codex transport metadata.

        A one-shot pilot-tool process has no persistent server object, so its
        invocation identity must be supplied again on every submission.  This
        check prevents a later abbreviated command from accepting an action
        while silently losing evidence that the same Codex thread was reused.
        """

        identity = self._identity
        if identity is None or identity.provider != "codex_subagent":
            return None
        if not identity.provider_invoked:
            return (
                "Codex pilot submission is missing --provider-invoked; use "
                "manual-json for a non-Codex response or repeat the complete "
                "verified invocation identity"
            )

        prior = [
            row
            for row in self._session.decisions
            if row.get("principal") == self._principal
            and row.get("provider_invoked") is True
        ]
        if not prior:
            return None

        continuity_fields = (
            ("provider", identity.provider),
            ("model", identity.model),
            ("reasoning_effort", identity.reasoning_effort),
            ("thread_id", identity.thread_id),
            ("thread_label", identity.thread_label),
        )
        for field_name, actual in continuity_fields:
            expected = {
                row.get(field_name)
                for row in prior
                if row.get(field_name) is not None
            }
            if len(expected) == 1 and actual != next(iter(expected)):
                return (
                    "Codex pilot invocation identity changed for "
                    f"{field_name}; reuse the original fixed-seat thread "
                    "and repeat its complete identity flags"
                )

        if any(
            row.get("provider_identity_verified") is True for row in prior
        ) and not identity.provider_identity_verified:
            return (
                "Codex pilot provider identity verification was dropped; "
                "repeat --provider-identity-verified"
            )
        if any(
            row.get("model_identity_verified") is True for row in prior
        ) and not identity.model_identity_verified:
            return (
                "Codex pilot model identity verification was dropped; "
                "repeat --model-identity-verified"
            )
        return None

    def _reject_pilot_response(
        self,
        response: Mapping[str, Any],
        error: str,
    ) -> dict[str, Any]:
        decision = self._session.state.pending_decision
        identity = self._identity
        capability = self._session.engine.permissions.capability_for(
            self._principal
        )
        actor_context = copy.deepcopy(
            decision.payload_by_actor.get(self._seat, {})
            if decision
            else {}
        )
        self._session.decisions.append(
            {
                "sequence": len(self._session.decisions) + 1,
                "decision_id": decision.decision_id if decision else None,
                "kind": decision.kind if decision else None,
                "role": "pilot",
                "actor": self._seat,
                "seat": self._seat,
                "principal": self._principal,
                "accepted": False,
                "action": response.get("action_id")
                or response.get("action")
                or response.get("a"),
                "plan": copy.deepcopy(response.get("actions") or response.get("plan")),
                "plan_category": (
                    response.get("plan")
                    if isinstance(response.get("plan"), str)
                    else None
                ),
                "reason": str(response.get("reason") or ""),
                "confidence": response.get("confidence"),
                "rejection": error,
                "provider": identity.provider if identity else None,
                "model": identity.model if identity else None,
                "reasoning_effort": (
                    identity.reasoning_effort if identity else None
                ),
                "thread_id": identity.thread_id if identity else None,
                "thread_label": identity.thread_label if identity else None,
                "parent_session_id": (
                    identity.parent_session_id if identity else None
                ),
                "provider_invoked": (
                    bool(identity.provider_invoked) if identity else False
                ),
                "provider_identity_verified": (
                    bool(identity.provider_identity_verified)
                    if identity
                    else False
                ),
                "model_identity_verified": (
                    bool(identity.model_identity_verified)
                    if identity
                    else False
                ),
                "model_configured": (
                    identity.model_configured if identity else None
                ),
                "reasoning_effort_configured": (
                    identity.reasoning_effort_configured
                    if identity
                    else None
                ),
                "thread_handle": (
                    identity.thread_id if identity else None
                ),
                "invoked_at": utc_now(),
                "invocation_id": (
                    identity.invocation_id if identity else None
                ),
                "metrics": {
                    key: value
                    for key, value in (
                        (
                            "input_tokens",
                            identity.input_tokens if identity else None,
                        ),
                        (
                            "cached_input_tokens",
                            (
                                identity.cached_input_tokens
                                if identity
                                else None
                            ),
                        ),
                        (
                            "output_tokens",
                            identity.output_tokens if identity else None,
                        ),
                        (
                            "reasoning_output_tokens",
                            (
                                identity.reasoning_output_tokens
                                if identity
                                else None
                            ),
                        ),
                        (
                            "latency_ms",
                            identity.latency_ms if identity else None,
                        ),
                    )
                    if value is not None
                },
                "retry_count": sum(
                    1
                    for row in self._session.decisions
                    if row.get("principal") == self._principal
                    and row.get("decision_id")
                    == (decision.decision_id if decision else None)
                    and row.get("accepted") is False
                ),
                "phase": self._session.state.phase,
                "step": self._session.state.step,
                "turn": self._session.state.turn_sequence,
                "legal_alternatives": (
                    self._session._legal_alternatives(
                        capability, actor_context
                    )
                    if capability is not None
                    else []
                ),
                "decision_context": actor_context,
            }
        )
        return {
            "accepted": False,
            "error": error,
            "retry": self._get_task_loaded(),
        }

    def _rule_visible(self, ref: str) -> bool:
        card = next(
            (
                value
                for value in self._session.state.cards.values()
                if value.ref == ref
            ),
            None,
        )
        if card is None or card.face_down:
            return False
        if card.zone == "library":
            return (
                self._seat in card.known_to
                or self._seat in card.revealed_to
            )
        if card.zone == "hand":
            return hand_card_is_known_to(self._session.engine, card, self._seat)
        return card.zone in {
            "battlefield",
            "graveyard",
            "exile",
            "command",
            "stack",
            "outside",
        }

    def get_rules(self, refs: list[str]) -> Any:
        if self._game_dir:
            with _record_lock(self._game_dir):
                self._reload()
                return self._get_rules_loaded(refs)
        return self._get_rules_loaded(refs)

    def _get_rules_loaded(self, refs: list[str]) -> Any:
        values = [str(ref) for ref in refs]
        if not values:
            return []
        rejected = [ref for ref in values if not self._rule_visible(ref)]
        if rejected:
            raise PermissionError(
                "Rules refs are not visible or legally known to this seat: "
                + ", ".join(rejected)
            )
        return self._session.rules(values, format="json")

    def get_profile(self) -> dict[str, Any] | None:
        if self._profile_cache is None:
            if self._game_dir:
                with _record_lock(self._game_dir):
                    self._reload()
                    value = self._session.pilot_profiles.get(
                        self._principal
                    )
            else:
                value = self._session.pilot_profiles.get(self._principal)
            self._profile_cache = copy.deepcopy(value) if value else {}
        return copy.deepcopy(self._profile_cache) or None

    @property
    def _memory_path(self) -> Path | None:
        if self._game_dir is None:
            return None
        return self._game_dir / "pilot-seat-memory" / f"{self._seat}.json"

    def get_memory(self) -> str:
        path = self._memory_path
        if path is None or not path.exists():
            return ""
        value = json.loads(path.read_text(encoding="utf-8"))
        return str(value.get("text") or "")[:500]

    def update_memory(self, text: str) -> dict[str, Any]:
        bounded = str(text)[:500]
        path = self._memory_path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                stable_json({"seat": self._seat, "text": bounded}),
                encoding="utf-8",
            )
        return {"accepted": True, "characters": len(bounded)}


class CoordinatorTools:
    """Public coordinator/arbiter surface with no pilot submission method."""

    def __init__(self, session: CommanderSession):
        self.session = session

    def status(self) -> dict[str, Any]:
        self.session.engine.pump()
        pending = self.session.pending_principals()
        public = self.session.packet("spectator", full=True)
        return {
            "game_id": self.session.state.game_id,
            "next_principal": pending[0] if pending else None,
            "public": public,
            "turn_sequence": self.session.state.turn_sequence,
            "game_over": self.session.state.game_over,
            "suppressed_meaningful_windows": sum(
                int(
                    player.stats.get("decision_optimization", {}).get(
                        "suppressed_meaningful_windows", 0
                    )
                )
                for player in self.session.state.players.values()
            ),
        }

    def get_arbiter_task(self) -> dict[str, Any] | None:
        if "arbiter" not in self.session.pending_principals():
            return None
        return self.session.packet("arbiter", full=True)

    def submit_arbiter(self, response: Mapping[str, Any]) -> dict[str, Any]:
        if "arbiter" not in self.session.pending_principals():
            return {"accepted": False, "error": "No arbiter task is pending"}
        result = self.session.act("arbiter", response)
        return {
            "accepted": result.ok,
            "error": None if result.ok else result.summary,
        }

    def fidelity(self) -> dict[str, Any]:
        return derive_review(
            self.session.engine,
            decisions=self.session.decisions,
        )["fidelity"]


@dataclass(slots=True)
class PilotThreadRecord:
    seat: str
    thread_label: str
    provider: str
    model: str | None
    reasoning_effort: str | None
    thread_id: str | None
    invocation_count: int = 0
    first_invocation_at: str | None = None
    last_invocation_at: str | None = None
    reused: bool = False
    retries: int = 0
    interruption_events: int = 0
    restart_events: int = 0
    provider_invoked: bool = False


class CodexThreadRegistry:
    """Coordinator-owned immutable seat-to-thread routing metadata."""

    REQUIRED_SEATS = ("A", "B", "C", "D")

    def __init__(
        self,
        *,
        parent_session_id: str | None = None,
    ):
        self.parent_session_id = parent_session_id
        self._threads: dict[str, PilotThreadRecord] = {}

    def register(
        self,
        *,
        seat: str,
        thread_label: str,
        provider: str,
        model: str | None,
        reasoning_effort: str | None,
        thread_id: str | None,
    ) -> None:
        if seat not in self.REQUIRED_SEATS:
            raise ValueError("Codex Commander arena seats are A, B, C, and D")
        if seat in self._threads:
            raise ValueError(f"Seat {seat} already has a persistent thread")
        if any(
            row.thread_label == thread_label
            for row in self._threads.values()
        ):
            raise ValueError("Pilot thread labels must be unique")
        if thread_id and any(
            row.thread_id == thread_id for row in self._threads.values()
        ):
            raise ValueError("One Codex thread cannot pilot multiple seats")
        self._threads[seat] = PilotThreadRecord(
            seat=seat,
            thread_label=thread_label,
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
            thread_id=thread_id,
        )

    def identity_for(self, seat: str) -> PilotInvocationIdentity:
        row = self._threads[seat]
        return PilotInvocationIdentity(
            provider=row.provider,
            model=row.model,
            reasoning_effort=row.reasoning_effort,
            thread_id=row.thread_id,
            thread_label=row.thread_label,
            parent_session_id=self.parent_session_id,
            provider_invoked=bool(row.thread_id),
        )

    def record_invocation(
        self,
        seat: str,
        *,
        thread_id: str | None,
        retries: int = 0,
    ) -> None:
        row = self._threads[seat]
        if row.thread_id != thread_id:
            row.restart_events += 1
            raise ValueError(
                f"Seat {seat} returned from a replacement thread; arena stopped"
            )
        now = utc_now()
        row.invocation_count += 1
        row.provider_invoked = True
        row.reused = row.invocation_count > 1
        row.first_invocation_at = row.first_invocation_at or now
        row.last_invocation_at = now
        row.retries += retries

    @classmethod
    def from_decisions(
        cls,
        decisions: list[Mapping[str, Any]],
        *,
        parent_session_id: str | None = None,
    ) -> "CodexThreadRegistry":
        """Reconstruct the immutable routing audit from accepted decisions."""

        registry = cls(parent_session_id=parent_session_id)
        for seat in cls.REQUIRED_SEATS:
            rows = [
                row
                for row in decisions
                if row.get("principal") == f"pilot:{seat}"
            ]
            if not rows:
                raise ValueError(f"No actual pilot invocation was recorded for seat {seat}")
            identity_fields = (
                "provider",
                "model",
                "reasoning_effort",
                "thread_id",
                "thread_label",
            )
            identity: dict[str, Any] = {}
            for field_name in identity_fields:
                values = {
                    row.get(field_name)
                    for row in rows
                    if row.get(field_name) is not None
                }
                if len(values) != 1:
                    raise ValueError(
                        f"Seat {seat} has inconsistent {field_name} metadata"
                    )
                identity[field_name] = next(iter(values), None)
            registry.register(
                seat=seat,
                thread_label=str(
                    identity["thread_label"] or f"quorune-pilot-{seat.lower()}"
                ),
                provider=str(identity["provider"] or "unavailable"),
                model=identity["model"],
                reasoning_effort=identity["reasoning_effort"],
                thread_id=identity["thread_id"],
            )
            record = registry._threads[seat]
            record.invocation_count = len(rows)
            timestamps = sorted(
                str(row["invoked_at"])
                for row in rows
                if row.get("invoked_at")
            )
            record.first_invocation_at = timestamps[0] if timestamps else None
            record.last_invocation_at = timestamps[-1] if timestamps else None
            record.reused = len(rows) > 1
            record.retries = sum(row.get("accepted") is False for row in rows)
            record.provider_invoked = all(
                bool(row.get("provider_invoked")) for row in rows
            )
        registry.assert_ready()
        return registry

    def assert_ready(self) -> None:
        missing = [
            seat for seat in self.REQUIRED_SEATS if seat not in self._threads
        ]
        if missing:
            raise ValueError(
                "Arena requires exactly four persistent pilot threads; missing "
                + ", ".join(missing)
            )

    def metadata(self) -> dict[str, Any]:
        self.assert_ready()
        rows = [asdict(self._threads[seat]) for seat in self.REQUIRED_SEATS]
        actual_codex = all(
            row["provider"] == "codex_subagent"
            and row["thread_id"]
            and row["invocation_count"] > 0
            and row["provider_invoked"]
            for row in rows
        )
        return {
            "parent_session_id": self.parent_session_id,
            "pilot_thread_count": len(rows),
            "persistent_thread_reuse": all(
                row["reused"] or row["invocation_count"] <= 1
                for row in rows
            ),
            "primary_made_strategic_decision": False,
            "provider_identity_verified": actual_codex,
            "model_identity_verified": actual_codex
            and all(row["model"] == "gpt-5.6-sol" for row in rows),
            "seat_projection_verified": True,
            "codex_subagent_run": actual_codex,
            "threads": rows,
            "nested_pilot_subagents": False,
        }


def _tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "get_task",
            "description": "Return this fixed seat's current projected task.",
            "inputSchema": {"type": "object", "additionalProperties": False},
        },
        {
            "name": "submit_action",
            "description": (
                "Submit a typed action for this fixed seat. Plan casing and "
                "reason/memory bounds are enforced before game mutation."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["plan", "reason"],
                "properties": {
                    "action_id": {"type": "string", "minLength": 1},
                    "actions": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 8,
                        "items": {
                            "type": "object",
                            "required": ["action_id"],
                            "properties": {
                                "action_id": {
                                    "type": "string",
                                    "minLength": 1,
                                },
                                "choices": {"type": "object"},
                                "future_choices": {
                                    "type": "object",
                                    "properties": {
                                        "search_card_name": {
                                            "type": "string"
                                        },
                                        "entry_pay_life": {
                                            "type": "boolean"
                                        },
                                    },
                                    "additionalProperties": False,
                                },
                            },
                            "additionalProperties": False,
                        },
                    },
                    "choices": {"type": "object"},
                    "plan": {
                        "type": "string",
                        "enum": list(PLAN_CATEGORIES),
                    },
                    "reason": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 180,
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "yield_mode": {
                        "type": ["string", "null"],
                        "enum": [
                            None,
                            "none",
                            "until_public_change",
                            "until_my_turn",
                            "auto_if_no_response",
                        ],
                    },
                    "memory_update": {
                        "type": "string",
                        "maxLength": 500,
                    },
                },
                "oneOf": [
                    {
                        "required": ["action_id"],
                        "not": {"required": ["actions"]},
                    },
                    {
                        "required": ["actions"],
                        "not": {"required": ["action_id"]},
                    },
                ],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_rules",
            "description": "Read rules for exact refs visible to this seat.",
            "inputSchema": {
                "type": "object",
                "required": ["refs"],
                "properties": {
                    "refs": {
                        "type": "array",
                        "items": {"type": "string"},
                    }
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "get_profile",
            "description": "Return this seat's exact validated pilot profile.",
            "inputSchema": {"type": "object", "additionalProperties": False},
        },
        {
            "name": "get_memory",
            "description": "Return this seat's bounded private strategic memory.",
            "inputSchema": {"type": "object", "additionalProperties": False},
        },
        {
            "name": "update_memory",
            "description": "Replace this seat's bounded strategic memory.",
            "inputSchema": {
                "type": "object",
                "required": ["text"],
                "properties": {"text": {"type": "string", "maxLength": 500}},
                "additionalProperties": False,
            },
        },
    ]


def run_pilot_mcp_stdio(
    tools: SeatScopedPilotTools,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> None:
    """Serve the six seat-scoped tools using MCP JSON-RPC over stdio."""

    source = input_stream or sys.stdin
    sink = output_stream or sys.stdout
    for line in source:
        if not line.strip():
            continue
        request = json.loads(line)
        request_id = request.get("id")
        method = request.get("method")
        params = dict(request.get("params") or {})
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": f"quorune-pilot-{tools.seat}",
                        "version": ENGINE_VERSION,
                    },
                }
            elif method == "notifications/initialized":
                continue
            elif method == "tools/list":
                result = {"tools": _tool_specs()}
            elif method == "tools/call":
                name = str(params.get("name") or "")
                arguments = dict(params.get("arguments") or {})
                if name not in PILOT_TOOL_NAMES:
                    raise ValueError(f"Unknown pilot tool {name!r}")
                if name == "get_task":
                    value = tools.get_task()
                elif name == "submit_action":
                    value = tools.submit_action(arguments)
                elif name == "get_rules":
                    value = tools.get_rules(list(arguments["refs"]))
                elif name == "get_profile":
                    value = tools.get_profile()
                elif name == "get_memory":
                    value = tools.get_memory()
                else:
                    value = tools.update_memory(str(arguments["text"]))
                result = {
                    "content": [
                        {"type": "text", "text": stable_json(value)}
                    ]
                }
            else:
                raise ValueError(f"Unsupported MCP method {method!r}")
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(exc)},
            }
        sink.write(stable_json(response) + "\n")
        sink.flush()
