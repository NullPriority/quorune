from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol

from .choice_forms import delegated_choice_fields
from .protocol import PROTOCOL_VERSION
from .rules.mana_pool_payment import mana_pool_choice_context
from .session import CommanderSession


COMMAND_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class CommandEnvelope:
    """Strict client-controlled command body.

    The authenticated principal is deliberately absent.  A transport adapter
    derives it from the guest/account session and supplies it separately to
    :class:`GameService`.
    """

    protocol_version: str
    game_id: str
    command_id: str
    decision_id: str
    action_id: str
    capability: str
    expected_view_revision: int
    choices: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CommandEnvelope":
        if not isinstance(value, Mapping):
            raise ValueError("Command envelope must be a JSON object")
        allowed = {
            "protocol_version",
            "game_id",
            "command_id",
            "decision_id",
            "action_id",
            "capability",
            "expected_view_revision",
            "choices",
        }
        unknown = sorted(str(key) for key in set(value) - allowed)
        if unknown:
            raise ValueError(
                "Command envelope contains unknown field(s): "
                + ", ".join(unknown)
            )
        missing = sorted(str(key) for key in allowed - set(value))
        if missing:
            raise ValueError(
                "Command envelope is missing field(s): "
                + ", ".join(missing)
            )
        choices = value.get("choices")
        if not isinstance(choices, Mapping):
            raise ValueError("Command choices must be a JSON object")
        revision = value.get("expected_view_revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise ValueError("expected_view_revision must be an integer")
        command_id = str(value.get("command_id") or "")
        if not COMMAND_ID_RE.fullmatch(command_id):
            raise ValueError("command_id has an invalid format")
        for name in (
            "protocol_version",
            "game_id",
            "decision_id",
            "action_id",
            "capability",
        ):
            raw = value.get(name)
            if not isinstance(raw, str) or not raw:
                raise ValueError(f"{name} must be a nonempty string")
        return cls(
            protocol_version=str(value["protocol_version"]),
            game_id=str(value["game_id"]),
            command_id=command_id,
            decision_id=str(value["decision_id"]),
            action_id=str(value["action_id"]),
            capability=str(value["capability"]),
            expected_view_revision=revision,
            choices=copy.deepcopy(dict(choices)),
        )

    def request_fingerprint(self) -> str:
        """Hash the idempotent request without retaining its bearer token."""

        payload = {
            "protocol_version": self.protocol_version,
            "game_id": self.game_id,
            "command_id": self.command_id,
            "decision_id": self.decision_id,
            "action_id": self.action_id,
            "expected_view_revision": self.expected_view_revision,
            "choices": self.choices,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    ok: bool
    code: str
    summary: str
    game_id: str
    command_id: str
    decision_id: str | None
    state_revision: int
    state_changed: bool
    event_ids: tuple[int, ...] = ()
    replayed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "summary": self.summary,
            "game_id": self.game_id,
            "command_id": self.command_id,
            "decision_id": self.decision_id,
            "state_revision": self.state_revision,
            "state_changed": self.state_changed,
            "event_ids": list(self.event_ids),
            "replayed": self.replayed,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CommandReceipt":
        return cls(
            ok=bool(value["ok"]),
            code=str(value["code"]),
            summary=str(value["summary"]),
            game_id=str(value["game_id"]),
            command_id=str(value["command_id"]),
            decision_id=(
                str(value["decision_id"])
                if value.get("decision_id") is not None
                else None
            ),
            state_revision=int(value["state_revision"]),
            state_changed=bool(value["state_changed"]),
            event_ids=tuple(int(item) for item in value.get("event_ids", [])),
            replayed=bool(value.get("replayed", False)),
        )


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    request_fingerprint: str
    receipt: CommandReceipt


class IdempotencyRepository(Protocol):
    def get(
        self, game_id: str, principal: str, command_id: str
    ) -> IdempotencyRecord | None: ...

    def put(
        self,
        game_id: str,
        principal: str,
        command_id: str,
        record: IdempotencyRecord,
    ) -> None: ...


class InMemoryIdempotencyRepository:
    """Thread-safe unit/development adapter.

    Durable adapters implement the same port.  Only a request hash and safe
    receipt are stored; the raw capability is never persisted here.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], IdempotencyRecord] = {}
        self._lock = threading.Lock()

    def get(
        self, game_id: str, principal: str, command_id: str
    ) -> IdempotencyRecord | None:
        with self._lock:
            return self._records.get((game_id, principal, command_id))

    def put(
        self,
        game_id: str,
        principal: str,
        command_id: str,
        record: IdempotencyRecord,
    ) -> None:
        key = (game_id, principal, command_id)
        with self._lock:
            existing = self._records.get(key)
            if existing is not None and existing != record:
                raise RuntimeError("Idempotency record changed after commit")
            self._records[key] = record


class GameService:
    """Transport-neutral command and projection application boundary."""

    def __init__(
        self,
        session: CommanderSession,
        *,
        idempotency: IdempotencyRepository | None = None,
    ) -> None:
        self.session = session
        self.idempotency = idempotency or InMemoryIdempotencyRepository()
        self._hydrate_idempotency_from_journal()

    def _hydrate_idempotency_from_journal(self) -> None:
        for command in self.session.commands:
            client_command_id = command.get("client_command_id")
            fingerprint = command.get("client_request_fingerprint")
            receipt = command.get("client_receipt")
            principal = command.get("principal")
            if not (
                isinstance(client_command_id, str)
                and isinstance(fingerprint, str)
                and isinstance(receipt, Mapping)
                and isinstance(principal, str)
            ):
                continue
            self.idempotency.put(
                self.session.state.game_id,
                principal,
                client_command_id,
                IdempotencyRecord(
                    request_fingerprint=fingerprint,
                    receipt=CommandReceipt.from_dict(receipt),
                ),
            )

    def observe(
        self,
        principal: str,
        *,
        full: bool = False,
        cursor_key: str | None = None,
    ) -> dict[str, Any]:
        return self.session.packet(
            principal, full=full, cursor_key=cursor_key
        )

    def drop_projection_cursor(self, cursor_key: str) -> None:
        self.session.drop_projection_cursor(cursor_key)

    def event_page(
        self,
        principal: str,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        self.session.projector.state = self.session.state
        return self.session.projector.event_page(
            principal,
            after=after,
            limit=limit,
        )

    def _receipt(
        self,
        envelope: CommandEnvelope,
        *,
        ok: bool,
        code: str,
        summary: str,
        state_changed: bool = False,
        event_ids: tuple[int, ...] = (),
    ) -> CommandReceipt:
        return CommandReceipt(
            ok=ok,
            code=code,
            summary=summary,
            game_id=self.session.state.game_id,
            command_id=envelope.command_id,
            decision_id=envelope.decision_id,
            state_revision=self.session.state.revision,
            state_changed=state_changed,
            event_ids=event_ids,
        )

    @staticmethod
    def _choice_fields(
        action: Mapping[str, Any],
        *,
        decision_kind: str,
        context: Mapping[str, Any],
    ) -> set[str]:
        """Return the fields the current server-issued action delegates.

        This is intentionally conservative.  Server-derived action metadata
        such as a card ref, source, cost, zone, or semantic key never becomes
        writable merely because it is present in the catalog entry.
        """

        return delegated_choice_fields(
            action,
            decision_kind=decision_kind,
            context=context,
        )

    def _selected_action(
        self, principal: str, action_id: str
    ) -> tuple[Mapping[str, Any] | None, str | None]:
        decision = self.session.state.pending_decision
        capability = self.session.engine.permissions.capability_for(principal)
        if decision is None or capability is None:
            return None, None
        actor_key = capability.actor or principal
        context = decision.payload_by_actor.get(actor_key, {})
        catalog = list(
            (context.get("legal") or {}).get("actions")
            or context.get("legal_actions")
            or (
                {"id": action, "action": action}
                for action in capability.allowed_actions
            )
        )
        selected = next(
            (
                item
                for item in catalog
                if isinstance(item, Mapping)
                and str(item.get("id") or "") == action_id
            ),
            None,
        )
        return selected, decision.kind

    def remember(
        self,
        envelope: CommandEnvelope,
        principal: str,
        receipt: CommandReceipt,
    ) -> CommandReceipt:
        self.idempotency.put(
            envelope.game_id,
            principal,
            envelope.command_id,
            IdempotencyRecord(
                request_fingerprint=envelope.request_fingerprint(),
                receipt=receipt,
            ),
        )
        return receipt

    def command(
        self,
        envelope: CommandEnvelope,
        *,
        principal: str,
        commit_idempotency: bool = True,
    ) -> CommandReceipt:
        if envelope.protocol_version != PROTOCOL_VERSION:
            return self._receipt(
                envelope,
                ok=False,
                code="unsupported_protocol",
                summary="Unsupported client protocol version",
            )
        if envelope.game_id != self.session.state.game_id:
            return self._receipt(
                envelope,
                ok=False,
                code="wrong_game",
                summary="Command belongs to a different game",
            )

        request_fingerprint = envelope.request_fingerprint()
        prior = self.idempotency.get(
            envelope.game_id, principal, envelope.command_id
        )
        if prior is not None:
            if prior.request_fingerprint != request_fingerprint:
                return self._receipt(
                    envelope,
                    ok=False,
                    code="idempotency_conflict",
                    summary="command_id was already used for a different request",
                )
            return replace(prior.receipt, replayed=True)

        if self.session.record_status not in {"created", "in_progress"}:
            status = self.session.record_status
            administrative_stop = (
                status == "paused"
                and (self.session.pause_reason or {}).get("kind")
                == "administrative_stop"
            )
            receipt = self._receipt(
                envelope,
                ok=False,
                code=(
                    "game_paused"
                    if status == "paused"
                    else "game_not_active"
                ),
                summary=(
                    "Game is administratively stopped; resume it before "
                    "submitting a new command"
                    if administrative_stop
                    else "Game is paused at a rules or fidelity boundary and "
                    "cannot accept new commands"
                    if status == "paused"
                    else f"Game is {status} and cannot accept new commands"
                ),
            )
            return (
                self.remember(envelope, principal, receipt)
                if commit_idempotency
                else receipt
            )

        decision = self.session.state.pending_decision
        if decision is None or decision.decision_id != envelope.decision_id:
            receipt = self._receipt(
                envelope,
                ok=False,
                code="stale_decision",
                summary="Decision is no longer current",
            )
            return self.remember(
                envelope,
                principal,
                receipt,
            ) if commit_idempotency else receipt
        if self.session.state.revision != envelope.expected_view_revision:
            receipt = self._receipt(
                envelope,
                ok=False,
                code="stale_view",
                summary="Projected view revision is stale; resynchronize",
            )
            return self.remember(
                envelope,
                principal,
                receipt,
            ) if commit_idempotency else receipt
        capability = self.session.engine.permissions.capability_for(principal)
        if capability is None or capability.token != envelope.capability:
            receipt = self._receipt(
                envelope,
                ok=False,
                code="unauthorized_capability",
                summary="Capability is unknown, stale, or unauthorized",
            )
            return self.remember(
                envelope,
                principal,
                receipt,
            ) if commit_idempotency else receipt
        selected, decision_kind = self._selected_action(
            principal, envelope.action_id
        )
        if selected is None or decision_kind is None:
            receipt = self._receipt(
                envelope,
                ok=False,
                code="stale_action",
                summary="Action is not in the current server-issued catalog",
            )
            return self.remember(
                envelope,
                principal,
                receipt,
            ) if commit_idempotency else receipt
        allowed_choices = self._choice_fields(
            selected,
            decision_kind=decision_kind,
            context=mana_pool_choice_context(
                self.session.state, capability.actor, decision_kind,
                decision.payload_by_actor.get(capability.actor or principal, {}),
            ),
        )
        unknown_choices = sorted(
            str(key) for key in set(envelope.choices) - allowed_choices
        )
        if unknown_choices:
            receipt = self._receipt(
                envelope,
                ok=False,
                code="invalid_choices",
                summary=(
                    "Choices contain field(s) not delegated by this action: "
                    + ", ".join(unknown_choices)
                ),
            )
            return self.remember(
                envelope,
                principal,
                receipt,
            ) if commit_idempotency else receipt

        result = self.session.act(
            principal,
            {
                "action_id": envelope.action_id,
                "choices": copy.deepcopy(dict(envelope.choices)),
            },
            client_command_id=envelope.command_id,
        )
        receipt = self._receipt(
            envelope,
            ok=result.ok,
            code="accepted" if result.ok else "action_rejected",
            summary=result.summary,
            state_changed=result.state_changed,
            event_ids=tuple(result.event_ids),
        )
        if result.ok and self.session.commands:
            command = self.session.commands[-1]
            if command.get("client_command_id") == envelope.command_id:
                command["client_request_fingerprint"] = request_fingerprint
                command["client_receipt"] = receipt.to_dict()
        return (
            self.remember(envelope, principal, receipt)
            if commit_idempotency
            else receipt
        )

    def poll(self) -> list[str]:
        if self.session.record_status not in {"created", "in_progress"}:
            return []
        self.session.engine.pump()
        return self.session.pending_principals()
