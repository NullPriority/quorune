from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
import time
import traceback
from typing import Any, Callable, Literal, Mapping, Protocol

from .service import CommandEnvelope, CommandReceipt, GameService
from .turn_priority_model import PriorityWindowHolds


LOG = logging.getLogger(__name__)


class GameActorClosed(RuntimeError):
    pass


class GameActorUnavailable(RuntimeError):
    """Raised after an actor can no longer promise durable acknowledgements."""


class GameLifecycleConflict(ValueError):
    """Raised when an administrative lifecycle transition is not allowed."""


class GamePersistence(Protocol):
    def save(self, service: GameService) -> Mapping[str, float] | None: ...


@dataclass(slots=True)
class _ActorMessage:
    kind: Literal[
        "observe",
        "public_events",
        "drop_cursor",
        "command",
        "poll",
        "inspect",
        "pause",
        "resume",
        "stop",
    ]
    future: asyncio.Future[Any]
    principal: str | None = None
    full: bool = False
    cursor_key: str | None = None
    include_lifecycle: bool = False
    envelope: CommandEnvelope | None = None
    reason: str | None = None
    after_event: int = 0
    limit: int = 100
    enqueued_at: float = field(default_factory=time.perf_counter)


class GameActor:
    """One serialized mailbox and writer for one active game."""

    def __init__(
        self,
        service: GameService,
        *,
        persistence: GamePersistence | None = None,
        queue_limit: int = 256,
        publish_command_change: Callable[[str], None] | None = None,
        priority_window_holds: Mapping[str, PriorityWindowHolds] | None = None,
    ) -> None:
        if queue_limit < 1:
            raise ValueError("queue_limit must be positive")
        self.service = service
        self.game_id = service.session.state.game_id
        self.persistence = persistence
        self._publish_command_change = publish_command_change
        self._priority_window_holds = dict(priority_window_holds or {})
        self._mailbox: asyncio.Queue[_ActorMessage] = asyncio.Queue(
            maxsize=queue_limit
        )
        self._admission_lock = asyncio.Lock()
        self._worker: asyncio.Task[None] | None = None
        self._closed = False
        self._failure: BaseException | None = None
        self.processed_messages = 0
        self._processing_kind: str | None = None
        self._persistence_pending = False
        self._persistence_started_at: float | None = None
        self._last_persistence_seconds: float | None = None
        self._last_authoritative_persistence_seconds: float | None = None
        self._last_derived_review_seconds: float | None = None
        self._last_command_by_principal: dict[str, dict[str, str | float | int | bool]] = {}

    @property
    def queue_depth(self) -> int:
        return self._mailbox.qsize()

    @property
    def requires_recovery(self) -> bool:
        return self._failure is not None

    @property
    def priority_window_holds(self) -> dict[str, PriorityWindowHolds]:
        return self._priority_window_holds.copy()

    def set_priority_window_holds(self, seat: str, holds: PriorityWindowHolds) -> None:
        if holds.steps or holds.preserve_empty:
            self._priority_window_holds[seat] = holds
        else:
            self._priority_window_holds.pop(seat, None)

    async def start(self) -> None:
        if self._closed:
            raise GameActorClosed(f"Game actor {self.game_id} is closed")
        if self._failure is not None:
            raise GameActorUnavailable(
                f"Game actor {self.game_id} requires recovery"
            )
        if self._worker is None:
            self._worker = asyncio.create_task(
                self._run(), name=f"game-actor:{self.game_id}"
            )

    async def _request(self, message: _ActorMessage) -> Any:
        async with self._admission_lock:
            await self.start()
            await self._mailbox.put(message)
        return await message.future

    async def observe(
        self,
        principal: str,
        *,
        full: bool = False,
        cursor_key: str | None = None,
        include_lifecycle: bool = False,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await self._request(
            _ActorMessage(
                kind="observe",
                future=loop.create_future(),
                principal=principal,
                full=full,
                cursor_key=cursor_key,
                include_lifecycle=include_lifecycle,
            )
        )

    async def drop_projection_cursor(self, cursor_key: str) -> None:
        loop = asyncio.get_running_loop()
        await self._request(
            _ActorMessage(
                kind="drop_cursor",
                future=loop.create_future(),
                cursor_key=cursor_key,
            )
        )

    async def public_events(
        self,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await self._request(
            _ActorMessage(
                kind="public_events",
                future=loop.create_future(),
                principal="spectator",
                after_event=after,
                limit=limit,
            )
        )

    async def command(
        self, principal: str, envelope: CommandEnvelope
    ) -> CommandReceipt:
        loop = asyncio.get_running_loop()
        return await self._request(
            _ActorMessage(
                kind="command",
                future=loop.create_future(),
                principal=principal,
                envelope=envelope,
            )
        )

    async def poll(self) -> list[str]:
        loop = asyncio.get_running_loop()
        return await self._request(
            _ActorMessage(kind="poll", future=loop.create_future())
        )

    def _lifecycle_summary(self) -> dict[str, Any]:
        session = self.service.session
        state = session.state
        pause_reason = session.pause_reason or {}
        status = (
            "complete"
            if state.game_over
            else "active"
            if session.record_status in {"created", "in_progress"}
            else session.record_status
        )
        return {
            "game_id": state.game_id,
            "status": status,
            "state_revision": state.revision,
            "turn_sequence": state.turn_sequence,
            "active_player": state.active_player,
            "phase": state.phase,
            "step": state.step,
            "pending_principals": session.pending_principals(),
            "game_over": state.game_over,
            "winner": state.winner,
            "draw": state.draw,
            "pause_reason": (
                {
                    "kind": str(pause_reason.get("kind") or "paused"),
                    "label": str(pause_reason.get("label") or "Game paused"),
                }
                if session.pause_reason is not None
                else None
            ),
            "commands": len(session.commands),
            "decisions": len(session.decisions),
            "events": len(state.events),
            "persistence": self.progress_snapshot()["persistence"],
        }

    def progress_snapshot(self, principal: str | None = None) -> dict[str, Any]:
        pending_seconds = (
            round(time.perf_counter() - self._persistence_started_at, 3)
            if self._persistence_pending
            and self._persistence_started_at is not None
            else None
        )
        timing = self._last_command_by_principal.get(principal) if principal is not None else None
        return {
            "processing_kind": self._processing_kind,
            "queue_depth": self.queue_depth,
            "last_command": dict(timing) if timing else None,
            "persistence": {
                "pending": self._persistence_pending,
                "pending_seconds": pending_seconds,
                "last_total_seconds": self._last_persistence_seconds,
                "last_authoritative_seconds": (
                    self._last_authoritative_persistence_seconds
                ),
                "last_derived_review_seconds": self._last_derived_review_seconds,
            },
        }

    async def _persist(self) -> None:
        if self.persistence is None:
            return
        self._persistence_pending = True
        self._persistence_started_at = time.perf_counter()
        try:
            timing = await asyncio.to_thread(
                self.persistence.save,
                self.service,
            )
            elapsed = time.perf_counter() - self._persistence_started_at
            self._last_persistence_seconds = round(elapsed, 6)
            if isinstance(timing, Mapping):
                authoritative = timing.get("authoritative_seconds")
                derived = timing.get("derived_review_seconds")
                self._last_authoritative_persistence_seconds = (
                    round(float(authoritative), 6)
                    if authoritative is not None
                    else None
                )
                self._last_derived_review_seconds = (
                    round(float(derived), 6)
                    if derived is not None
                    else None
                )
        finally:
            if self._persistence_started_at is not None:
                self._last_persistence_seconds = round(
                    time.perf_counter() - self._persistence_started_at,
                    6,
                )
            self._persistence_pending = False
            self._persistence_started_at = None

    async def inspect(self) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await self._request(
            _ActorMessage(kind="inspect", future=loop.create_future())
        )

    async def pause(self, reason: str) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await self._request(
            _ActorMessage(
                kind="pause",
                future=loop.create_future(),
                reason=reason,
            )
        )

    async def resume(self) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await self._request(
            _ActorMessage(kind="resume", future=loop.create_future())
        )

    async def _handle_command(self, message: _ActorMessage) -> CommandReceipt:
        if message.envelope is None:
            raise RuntimeError("Command message is missing envelope")
        principal = str(message.principal)
        started = time.perf_counter()
        result = await asyncio.to_thread(
            self.service.command,
            message.envelope,
            principal=principal,
            commit_idempotency=False,
            **({"priority_window_holds": self.priority_window_holds} if self._priority_window_holds else {}),
        )
        executed = time.perf_counter()
        persistence_ms = 0.0
        if (
            result.ok
            and result.state_changed
            and not result.replayed
            and self.persistence is not None
        ):
            await self._persist()
            persistence_ms = (time.perf_counter() - executed) * 1000
        if not result.replayed and result.code != "invalid_envelope":
            self.service.remember(
                message.envelope,
                principal,
                result,
            )
        self._last_command_by_principal[principal] = {
            "interaction_id": message.envelope.command_id,
            "revision": result.state_revision,
            "replayed": result.replayed,
            "queue_ms": round((started - message.enqueued_at) * 1000, 2),
            "execution_ms": round((executed - started) * 1000, 2),
            "persistence_ms": round(persistence_ms, 2),
            "actor_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        if self._publish_command_change and result.ok and result.state_changed and not result.replayed:
            self._publish_command_change(self.game_id)
        return result

    async def _run(self) -> None:
        while True:
            message = await self._mailbox.get()
            self._processing_kind = message.kind
            try:
                if message.kind == "stop":
                    if not message.future.done():
                        message.future.set_result(None)
                    return
                if self._failure is not None:
                    raise GameActorUnavailable(
                        f"Game actor {self.game_id} requires recovery"
                    )
                if message.kind == "observe":
                    result = self.service.observe(
                        str(message.principal),
                        full=message.full,
                        cursor_key=message.cursor_key,
                    )
                    if message.include_lifecycle:
                        result = {"packet": result, "lifecycle": self._lifecycle_summary()}
                elif message.kind == "public_events":
                    result = self.service.event_page(
                        "spectator",
                        after=message.after_event,
                        limit=message.limit,
                    )
                elif message.kind == "drop_cursor":
                    if message.cursor_key is None:
                        raise RuntimeError("Projection cursor key is missing")
                    self.service.drop_projection_cursor(message.cursor_key)
                    result = None
                elif message.kind == "command":
                    result = await self._handle_command(message)
                elif message.kind == "poll":
                    result = self.service.poll()
                elif message.kind == "inspect":
                    result = self._lifecycle_summary()
                elif message.kind == "pause":
                    session = self.service.session
                    if (
                        session.state.game_over
                        or session.record_status
                        not in {"created", "in_progress", "paused"}
                    ):
                        raise GameLifecycleConflict(
                            "A finished, aborted, or corrupt game cannot be "
                            "stopped"
                        )
                    if session.record_status == "paused":
                        if (session.pause_reason or {}).get("kind") != (
                            "administrative_stop"
                        ):
                            raise GameLifecycleConflict(
                                "A fidelity or rules pause cannot be replaced "
                                "by an administrative stop"
                            )
                        result = {
                            **self._lifecycle_summary(),
                            "changed": False,
                        }
                    else:
                        session.pause(
                            {
                                "kind": "administrative_stop",
                                "label": str(message.reason or "Match stopped")[
                                    :500
                                ],
                            }
                        )
                        if self.persistence is not None:
                            await self._persist()
                        result = {
                            **self._lifecycle_summary(),
                            "changed": True,
                        }
                elif message.kind == "resume":
                    session = self.service.session
                    if session.state.game_over or session.record_status in {
                        "complete",
                        "aborted",
                    }:
                        raise GameLifecycleConflict(
                            "A finished game cannot be resumed"
                        )
                    if session.record_status in {"created", "in_progress"}:
                        result = {
                            **self._lifecycle_summary(),
                            "changed": False,
                        }
                    else:
                        if (session.pause_reason or {}).get("kind") != (
                            "administrative_stop"
                        ):
                            raise GameLifecycleConflict(
                                "Only an administrative stop can be resumed "
                                "from the browser"
                            )
                        session.resume()
                        if self.persistence is not None:
                            await self._persist()
                        result = {
                            **self._lifecycle_summary(),
                            "changed": True,
                        }
                else:  # pragma: no cover - Literal and constructor guard this.
                    raise RuntimeError(f"Unknown actor message {message.kind}")
                self.processed_messages += 1
                if not message.future.done():
                    message.future.set_result(result)
            except BaseException as exc:
                response_exception = exc
                durable_failure = (
                    message.kind == "command"
                    or (
                        message.kind in {"pause", "resume"}
                        and not isinstance(exc, GameLifecycleConflict)
                    )
                ) and not isinstance(exc, GameActorUnavailable)
                if durable_failure:
                    # A command exception can occur after in-memory mutation
                    # but before durable save/idempotency completion. Stop all
                    # further traffic until a fresh actor reloads durable state.
                    self._failure = exc
                    frames = traceback.extract_tb(exc.__traceback__)
                    origin = frames[-1] if frames else None
                    LOG.error(
                        "Game actor %s failed during %s: %s at %s:%s (%s)",
                        self.game_id, message.kind, type(exc).__name__,
                        origin.filename if origin else "unknown",
                        origin.lineno if origin else 0,
                        origin.name if origin else "unknown",
                    )
                    if message.kind == "command":
                        if self._publish_command_change:
                            self._publish_command_change(self.game_id)
                        response_exception = GameActorUnavailable(
                            f"Game actor {self.game_id} failed durable command "
                            "commit and requires recovery"
                        )
                    else:
                        response_exception = GameActorUnavailable(
                            f"Game actor {self.game_id} failed durable "
                            "lifecycle commit and requires recovery"
                        )
                if not message.future.done():
                    # A caller can clear traceback frames while handling its
                    # exception. Keep the live worker frame out of that trace.
                    message.future.set_exception(response_exception.with_traceback(None))
            finally:
                self._processing_kind = None
                self._mailbox.task_done()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        worker = self._worker
        if worker is None:
            return
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        # A backpressured request that already passed start() goes ahead of
        # the stop message. Later requests see _closed when admitted.
        async with self._admission_lock:
            await self._mailbox.put(_ActorMessage(kind="stop", future=future))
        await future
        await worker
        self._worker = None


class GameManager:
    """Own the unique actor route for every active in-process game."""

    def __init__(self) -> None:
        self._actors: dict[str, GameActor] = {}
        self._lock = asyncio.Lock()

    async def add(
        self,
        service: GameService,
        *,
        persistence: GamePersistence | None = None,
        publish_command_change: Callable[[str], None] | None = None,
        priority_window_holds: Mapping[str, PriorityWindowHolds] | None = None,
    ) -> GameActor:
        game_id = service.session.state.game_id
        async with self._lock:
            if game_id in self._actors:
                raise ValueError(f"Game {game_id} already has an actor")
            actor = GameActor(
                service, persistence=persistence, publish_command_change=publish_command_change,
                priority_window_holds=priority_window_holds,
            )
            self._actors[game_id] = actor
        await actor.start()
        return actor

    def get(self, game_id: str) -> GameActor:
        try:
            return self._actors[game_id]
        except KeyError as exc:
            raise KeyError(f"Unknown active game {game_id}") from exc

    async def remove(self, game_id: str) -> None:
        async with self._lock:
            actor = self._actors.pop(game_id, None)
        if actor is not None:
            await actor.close()

    async def close(self) -> None:
        async with self._lock:
            actors = list(self._actors.values())
            self._actors.clear()
        await asyncio.gather(*(actor.close() for actor in actors))

    @property
    def game_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._actors))
