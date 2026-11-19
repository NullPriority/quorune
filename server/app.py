from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import time
from typing import Any, AsyncIterator, Literal
from urllib.parse import urlsplit
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from quorune import (
    __version__,
    CardDatabase,
    CommandEnvelope,
    CommandReceipt,
    CommanderSession,
    DeckLoader,
    DirectoryGamePersistence,
    GameConfig,
    GameManager,
    GameService,
    SqliteIdempotencyRepository,
    deck_list_fingerprint,
    parse_deck_text,
)
from quorune.preflight import semantic_preflight
from quorune.record import database_fingerprint
from quorune.rules.evidence import _capability_validation_snapshot
from quorune.runtime import (
    GameActor,
    GameLifecycleConflict,
    GamePersistence,
)
from quorune.deck import MoxfieldFetchError, is_moxfield_source

from .store import (
    ServerStore,
    StoreConflict,
    StoreForbidden,
    StoreNotFound,
)
from .data import CardImageCache, IMAGE_SIZES, ManagedScryfallData


COOKIE_NAME = "commander_guest"
CSRF_COOKIE_NAME = "commander_csrf"
TAB_HEADER_NAME = "X-Commander-Tab"
TAB_COOKIE_PREFIX = f"{COOKIE_NAME}_tab_"
TAB_PROTOCOL_PREFIX = "commander.tab."
TAB_ID_RE = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True, slots=True)
class ServerSettings:
    card_db: Path
    database: Path
    game_root: Path
    bulk_dir: Path = Path("data/bulk")
    card_snapshot_dir: Path = Path("data/card-snapshots")
    image_cache: Path = Path("data/images")
    static_dir: Path = Path("web/dist")
    auto_update_cards: bool = False
    card_update_interval_seconds: int = 24 * 60 * 60
    allowed_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    secure_cookies: bool = False

    @classmethod
    def from_environment(cls) -> "ServerSettings":
        data_root = Path(os.environ.get("MTG_SERVER_DATA", "local/server"))
        managed_data_root = Path(os.environ.get("MTG_DATA_ROOT", "data"))
        explicit_card_db = "MTG_CARD_DB" in os.environ
        return cls(
            card_db=Path(os.environ.get("MTG_CARD_DB", managed_data_root / "scryfall-current.sqlite3")),
            database=data_root / "server.sqlite3",
            game_root=data_root / "games",
            bulk_dir=Path(os.environ.get("MTG_BULK_DIR", managed_data_root / "bulk")),
            card_snapshot_dir=Path(
                os.environ.get("MTG_CARD_SNAPSHOT_DIR", managed_data_root / "card-snapshots")
            ),
            image_cache=Path(os.environ.get("MTG_IMAGE_CACHE", managed_data_root / "images")),
            static_dir=Path(os.environ.get("MTG_WEB_DIST", "web/dist")),
            auto_update_cards=os.environ.get(
                "MTG_AUTO_UPDATE_CARDS", "0" if explicit_card_db else "1"
            ) == "1",
            card_update_interval_seconds=int(
                os.environ.get("MTG_CARD_UPDATE_SECONDS", str(24 * 60 * 60))
            ),
            allowed_origins=tuple(
                origin.strip()
                for origin in os.environ.get(
                    "MTG_ALLOWED_ORIGINS",
                    "http://localhost:5173,http://127.0.0.1:5173",
                ).split(",")
                if origin.strip()
            ),
            secure_cookies=os.environ.get("MTG_SECURE_COOKIES") == "1",
        )


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GuestRequest(StrictModel):
    display_name: str = Field(min_length=1, max_length=40, pattern=r"^[^\x00-\x1f]+$")

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Display name cannot be blank")
        return normalized


class RoomRequest(StrictModel):
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)
    player_count: Literal[2, 4] = 4


class JoinRoomRequest(StrictModel):
    invite_code: str = Field(min_length=20, max_length=64)
    seat: str = Field(pattern=r"^[A-Da-d]$")


class WatchRoomRequest(StrictModel):
    invite_code: str = Field(min_length=20, max_length=64)


class DeckRequest(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    commander: str = Field(default="", max_length=200)
    decklist: str = Field(default="", max_length=100_000)
    source_url: str | None = Field(default=None, max_length=500)
    legality_confirmation: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_source(self) -> "DeckRequest":
        has_text = bool(self.decklist.strip())
        has_url = bool((self.source_url or "").strip())
        if has_text == has_url:
            raise ValueError("Supply exactly one of decklist or source_url")
        if has_text and not self.commander.strip():
            raise ValueError("Text deck imports require a commander")
        return self


class StopGameRequest(StrictModel):
    reason: str = Field(
        default="Match stopped by the room owner",
        min_length=1,
        max_length=500,
    )

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Stop reason cannot be blank")
        return normalized


class ResumeGameRequest(StrictModel):
    pass


def _server_game_status(service: GameService) -> str:
    session = service.session
    if session.state.game_over:
        return "complete"
    if session.record_status in {"created", "in_progress"}:
        return "active"
    return session.record_status


def _pause_browser_rules_boundary(service: GameService) -> bool:
    """Fail closed when a browser game requires the separate arbiter role.

    The transport-neutral engine intentionally supports explicit arbiter
    clients.  The browser table does not: its authenticated principals are
    seated pilots or spectators.  Leaving an arbiter-only decision active
    therefore makes every player appear to be waiting on priority forever.
    """

    session = service.session
    decision = session.state.pending_decision
    if (
        session.record_status not in {"created", "in_progress"}
        or decision is None
        or decision.role != "arbiter"
    ):
        return False
    arbiter_payload = decision.payload_by_actor.get("arbiter", {})
    label = str(
        arbiter_payload.get("label") or "the current stack item"
    )[:200]
    session.pause(
        {
            "kind": "browser_rules_boundary",
            "label": (
                f"Rules support is required before {label} can continue. "
                "The match is paused; no player is passing priority."
            ),
            "decision_kind": decision.kind,
            "decision_id": decision.decision_id,
        }
    )
    return True


class _BrowserGameService(GameService):
    """Game service that enforces the browser's no-arbiter boundary."""

    def command(
        self,
        envelope: CommandEnvelope,
        *,
        principal: str,
        commit_idempotency: bool = True,
    ) -> CommandReceipt:
        receipt = super().command(
            envelope,
            principal=principal,
            commit_idempotency=commit_idempotency,
        )
        if receipt.ok:
            _pause_browser_rules_boundary(self)
        return receipt

    def poll(self) -> list[str]:
        principals = super().poll()
        return [] if _pause_browser_rules_boundary(self) else principals


class _StoreGamePersistence(GamePersistence):
    def __init__(
        self, records: DirectoryGamePersistence, store: ServerStore
    ) -> None:
        self.records = records
        self.store = store

    def save(self, service: GameService) -> dict[str, float]:
        started = time.perf_counter()
        _pause_browser_rules_boundary(service)
        timing = dict(self.records.save(service))
        store_started = time.perf_counter()
        self.store.update_game_state(
            service.session.state.game_id,
            service.session.state.revision,
            _server_game_status(service),
        )
        timing["authoritative_seconds"] = (
            float(timing.get("authoritative_seconds") or 0.0)
            + time.perf_counter()
            - store_started
        )
        timing["total_seconds"] = time.perf_counter() - started
        return timing


class ProjectionHub:
    def __init__(self) -> None:
        self._versions: dict[str, int] = {}
        self._events: dict[str, asyncio.Event] = {}

    def version(self, game_id: str) -> int:
        return self._versions.get(game_id, 0)

    async def notify(self, game_id: str) -> None:
        self._versions[game_id] = self.version(game_id) + 1
        event = self._events.pop(game_id, None)
        if event is not None:
            event.set()

    async def wait(self, game_id: str, after: int, timeout: float = 15) -> int:
        if self.version(game_id) > after:
            return self.version(game_id)
        event = self._events.setdefault(game_id, asyncio.Event())
        # No await occurs between the version check and event registration, so
        # notify() cannot race past this waiter on the event loop. Event waits
        # also cancel cleanly when a WebSocket disconnects; Condition.wait()
        # under wait_for() could fail while reacquiring its lock on affected
        # runtimes.
        if self.version(game_id) <= after:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        return self.version(game_id)


class ServerRuntime:
    def __init__(self, settings: ServerSettings) -> None:
        self.settings = settings
        self.store = ServerStore(settings.database)
        self.card_db = CardDatabase(settings.card_db)
        self._card_databases: dict[str, CardDatabase] = {
            str(database_fingerprint(self.card_db)["metadata_hash"]): self.card_db
        }
        self.records = DirectoryGamePersistence(settings.game_root)
        self.idempotency = SqliteIdempotencyRepository(settings.database)
        self.persistence = _StoreGamePersistence(self.records, self.store)
        self.manager = GameManager()
        self.hub = ProjectionHub()
        self._load_lock = asyncio.Lock()
        self._background: set[asyncio.Task[Any]] = set()

    def spawn(self, coroutine: Any, *, name: str) -> None:
        task = asyncio.create_task(coroutine, name=name)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def actor(self, game_id: str) -> GameActor:
        try:
            return self.manager.get(game_id)
        except KeyError:
            pass
        async with self._load_lock:
            try:
                return self.manager.get(game_id)
            except KeyError:
                loaded = self.records.load(
                    self._database_for_game(game_id),
                    game_id,
                    idempotency=self.idempotency,
                )
                service = _BrowserGameService(
                    loaded.session,
                    idempotency=self.idempotency,
                )
                if _pause_browser_rules_boundary(service):
                    self.records.save(service)
                self.store.update_game_state(
                    game_id,
                    service.session.state.revision,
                    _server_game_status(service),
                )
                return await self.manager.add(
                    service, persistence=self.persistence
                )

    def _database_for_game(self, game_id: str) -> CardDatabase:
        manifest_path = self.records.game_directory(game_id) / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            expected = str(manifest.get("scryfall", {}).get("metadata_hash") or "")
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            raise ValueError(f"Persisted game manifest is unreadable: {exc}") from exc
        if not expected:
            raise ValueError("Persisted game does not pin a card-database fingerprint")
        existing = self._card_databases.get(expected)
        if existing is not None:
            return existing
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Persisted game card-database fingerprint is invalid")
        path = self.settings.card_snapshot_dir / f"{expected}.sqlite3"
        if not path.is_file():
            raise ValueError(
                "The card-database snapshot required by this Game Record is unavailable"
            )
        database = CardDatabase(path)
        actual = str(database_fingerprint(database)["metadata_hash"])
        if actual != expected:
            database.close()
            raise ValueError("Retained card-database snapshot fingerprint mismatch")
        self._card_databases[expected] = database
        return database

    def card_databases(self) -> tuple[CardDatabase, ...]:
        return tuple(self._card_databases.values())

    async def game_summary(
        self, game_id: str, guest_id: str
    ) -> dict[str, Any]:
        # Authorize before a request can cause a persisted game to be loaded.
        self.store.game_membership(game_id, guest_id)
        actor = await self.actor(game_id)
        control = self.store.game_summary(game_id, guest_id)
        lifecycle = await actor.inspect()
        return {
            **control,
            **lifecycle,
            "can_stop": bool(
                control["owner"] and lifecycle["status"] == "active"
            ),
            "can_resume": bool(
                control["owner"]
                and lifecycle["status"] == "paused"
                and (lifecycle.get("pause_reason") or {}).get("kind")
                == "administrative_stop"
            ),
        }

    async def close(self) -> None:
        for task in self._background:
            task.cancel()
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)
        await self.manager.close()
        for database in set(self._card_databases.values()):
            database.close()


def _tab_cookie_name(tab_id: str) -> str:
    return f"{TAB_COOKIE_PREFIX}{tab_id}"


def _request_tab_id(request: Request) -> str | None:
    value = request.headers.get(TAB_HEADER_NAME, "").lower()
    return value if TAB_ID_RE.fullmatch(value) else None


def _websocket_origin_allowed(
    origin: str | None,
    host: str,
    configured_origins: tuple[str, ...],
) -> bool:
    if not origin:
        return True
    if origin in configured_origins:
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(host)
        and parsed.netloc.lower() == host.lower()
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
    )


def _bearer(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        return authorization[7:]
    tab_id = _request_tab_id(request)
    if tab_id:
        # A browser tab that presents a valid selector must never fall back to
        # another tab's shared legacy cookie.
        return request.cookies.get(_tab_cookie_name(tab_id))
    return request.cookies.get(COOKIE_NAME)


def _runtime(request: Request) -> ServerRuntime:
    runtime: ServerRuntime | None = request.app.state.runtime
    if runtime is None:
        raise HTTPException(
            status_code=503,
            detail="Card data is still being prepared; check /api/v1/system",
        )
    return runtime


def _guest(
    request: Request,
    runtime: ServerRuntime = Depends(_runtime),
) -> dict[str, Any]:
    try:
        return runtime.store.authenticate(_bearer(request) or "")
    except StoreForbidden as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _csrf(
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> None:
    if request.headers.get("authorization", "").startswith("Bearer "):
        return
    cookie = request.cookies.get(CSRF_COOKIE_NAME)
    if (
        not cookie
        or not x_csrf_token
        or not hmac.compare_digest(cookie, x_csrf_token)
    ):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")


def _translate_store_error(exc: BaseException) -> HTTPException:
    if isinstance(exc, StoreForbidden):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, StoreNotFound):
        return HTTPException(status_code=404, detail=str(exc.args[0]))
    if isinstance(exc, StoreConflict):
        return HTTPException(status_code=409, detail=str(exc))
    raise exc


async def _inspect_game_payload(
    runtime: ServerRuntime, game_id: str, guest_id: str
) -> dict[str, Any]:
    try:
        return {"game": await runtime.game_summary(game_id, guest_id)}
    except (StoreForbidden, StoreNotFound) as exc:
        raise _translate_store_error(exc) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _game_progress_payload(
    runtime: ServerRuntime, game_id: str, guest_id: str
) -> dict[str, Any]:
    """Return public actor/persistence progress without mailbox queuing."""

    try:
        runtime.store.game_membership(game_id, guest_id)
        actor = await runtime.actor(game_id)
        return actor.progress_snapshot()
    except (StoreForbidden, StoreNotFound) as exc:
        raise _translate_store_error(exc) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _register_game_progress_route(app: FastAPI) -> None:
    @app.get("/api/v1/games/{game_id}/progress")
    async def game_progress(
        game_id: str,
        guest: dict[str, Any] = Depends(_guest),
        runtime: ServerRuntime = Depends(_runtime),
    ) -> dict[str, Any]:
        return await _game_progress_payload(
            runtime, game_id, guest["guest_id"]
        )


def _compact_preflight(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "trusted_only_ready": bool(report.get("trusted_only_ready")),
        "deck_review_eligible_possible": bool(
            report.get("deck_review_eligible_possible")
        ),
        "fully_playable_cards": report.get("fully_playable_cards"),
        "partial_cards": report.get("partial_cards"),
        "unresolved_cards": report.get("unresolved_cards"),
        "oracle_residual_gate_pass": bool(
            report.get("oracle_residual_gate_pass")
        ),
    }


def _legality_confirmation_fingerprint(
    deck_fingerprint: str,
    issues: list[dict[str, Any]],
) -> str:
    payload = json.dumps(
        {
            "schema_version": 1,
            "deck_list_fingerprint": deck_fingerprint,
            "issues": issues,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_app(settings: ServerSettings | None = None) -> FastAPI:
    resolved = settings or ServerSettings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.runtime = None
        runtime_lock = asyncio.Lock()

        async def initialize_runtime(_database: Path) -> None:
            async with runtime_lock:
                if app.state.runtime is None:
                    app.state.runtime = ServerRuntime(resolved)

        data = ManagedScryfallData(
            resolved.card_db,
            resolved.bulk_dir,
            resolved.card_snapshot_dir,
            resolved.game_root,
            enabled=resolved.auto_update_cards,
            interval_seconds=resolved.card_update_interval_seconds,
        )
        images = CardImageCache(
            resolved.image_cache,
            lambda: (
                app.state.runtime.card_databases()
                if app.state.runtime is not None
                else ()
            ),
        )
        app.state.data = data
        app.state.images = images
        await data.start(initialize_runtime)
        try:
            yield
        finally:
            await data.close()
            runtime: ServerRuntime | None = app.state.runtime
            if runtime is not None:
                await runtime.close()

    app = FastAPI(
        title="Quorune Server",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token", TAB_HEADER_NAME],
    )

    @app.get("/api/v1/health")
    async def health(request: Request) -> dict[str, str]:
        data: ManagedScryfallData = request.app.state.data
        return {
            "status": "ok" if data.ready else "starting",
            "authority": "server",
            "protocol": "3.0",
        }

    @app.get("/api/v1/system")
    async def system_status(request: Request) -> dict[str, Any]:
        data: ManagedScryfallData = request.app.state.data
        images: CardImageCache = request.app.state.images
        return {
            "server": "ready" if data.ready else "starting",
            "protocol": "3.0",
            "card_data": data.status(),
            "images": {
                "mode": "local_on_demand_cache",
                "downloaded": images.downloaded,
                "ready": data.ready,
            },
            "browser": {
                "served_by_server": (resolved.static_dir / "index.html").is_file()
            },
        }

    @app.post("/api/v1/system/refresh", status_code=202)
    async def refresh_system(request: Request) -> dict[str, Any]:
        client_host = request.client.host if request.client else ""
        if client_host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(status_code=403, detail="Refresh is limited to the local machine")
        data: ManagedScryfallData = request.app.state.data
        if not data.enabled:
            raise HTTPException(status_code=409, detail="Automatic card-data updates are disabled")
        data.request_refresh()
        return {"accepted": True, "card_data": data.status()}

    @app.get("/api/v1/cards/{oracle_prefix}/image")
    async def card_image(
        oracle_prefix: str,
        request: Request,
        face: int = 0,
        size: str = "normal",
    ) -> FileResponse:
        if not re.fullmatch(r"[0-9a-fA-F-]{8,36}", oracle_prefix):
            raise HTTPException(status_code=404, detail="Unknown card image")
        if face < 0 or face > 9 or size not in IMAGE_SIZES:
            raise HTTPException(status_code=422, detail="Unsupported card image variant")
        images: CardImageCache = request.app.state.images
        try:
            path, media_type = await images.get(oracle_prefix, face=face, size=size)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @app.post("/api/v1/guests", status_code=201)
    async def create_guest(
        body: GuestRequest,
        request: Request,
        response: Response,
        runtime: ServerRuntime = Depends(_runtime),
    ) -> dict[str, Any]:
        guest, token = runtime.store.create_guest(body.display_name)
        csrf = secrets.token_urlsafe(24)
        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            secure=resolved.secure_cookies,
            samesite="strict",
            max_age=7 * 24 * 60 * 60,
            path="/",
        )
        tab_id = _request_tab_id(request)
        if tab_id:
            response.set_cookie(
                _tab_cookie_name(tab_id),
                token,
                httponly=True,
                secure=resolved.secure_cookies,
                samesite="strict",
                max_age=7 * 24 * 60 * 60,
                path="/",
            )
        response.set_cookie(
            CSRF_COOKIE_NAME,
            csrf,
            httponly=False,
            secure=resolved.secure_cookies,
            samesite="strict",
            max_age=7 * 24 * 60 * 60,
            path="/",
        )
        payload: dict[str, Any] = {"guest": guest, "csrf_token": csrf}
        # CLI/test clients without a browser-tab selector may explicitly use a
        # bearer. Browser JavaScript receives only the HttpOnly cookie.
        if tab_id is None:
            payload["access_token"] = token
        return payload

    @app.get("/api/v1/me")
    async def me(guest: dict[str, Any] = Depends(_guest)) -> dict[str, Any]:
        return {"guest": guest}

    @app.post("/api/v1/rooms", status_code=201, dependencies=[Depends(_csrf)])
    async def create_room(
        body: RoomRequest,
        guest: dict[str, Any] = Depends(_guest),
        runtime: ServerRuntime = Depends(_runtime),
    ) -> dict[str, Any]:
        seed = body.seed if body.seed is not None else secrets.randbits(63)
        room, invite = runtime.store.create_room(
            guest["guest_id"],
            seed=seed,
            player_count=body.player_count,
        )
        return {"room": room, "invite_code": invite}

    @app.post("/api/v1/rooms/join", dependencies=[Depends(_csrf)])
    async def join_room(
        body: JoinRoomRequest,
        guest: dict[str, Any] = Depends(_guest),
        runtime: ServerRuntime = Depends(_runtime),
    ) -> dict[str, Any]:
        try:
            room = runtime.store.join_room(
                guest["guest_id"],
                invite_code=body.invite_code,
                seat=body.seat,
            )
        except (StoreForbidden, StoreNotFound, StoreConflict) as exc:
            raise _translate_store_error(exc) from exc
        return {"room": room}

    @app.post("/api/v1/rooms/watch", dependencies=[Depends(_csrf)])
    async def watch_room(
        body: WatchRoomRequest,
        guest: dict[str, Any] = Depends(_guest),
        runtime: ServerRuntime = Depends(_runtime),
    ) -> dict[str, Any]:
        try:
            room = runtime.store.join_spectator(
                guest["guest_id"],
                invite_code=body.invite_code,
            )
        except (StoreForbidden, StoreNotFound, StoreConflict) as exc:
            raise _translate_store_error(exc) from exc
        return {"room": room}

    @app.get("/api/v1/rooms/{room_id}")
    async def get_room(
        room_id: str,
        guest: dict[str, Any] = Depends(_guest),
        runtime: ServerRuntime = Depends(_runtime),
    ) -> dict[str, Any]:
        try:
            return {"room": runtime.store.room(room_id, guest["guest_id"])}
        except (StoreForbidden, StoreNotFound) as exc:
            raise _translate_store_error(exc) from exc

    @app.post(
        "/api/v1/rooms/{room_id}/invite",
        dependencies=[Depends(_csrf)],
    )
    async def rotate_room_invite(
        room_id: str,
        guest: dict[str, Any] = Depends(_guest),
        runtime: ServerRuntime = Depends(_runtime),
    ) -> dict[str, str]:
        try:
            invite_code = runtime.store.rotate_invite(
                room_id,
                guest["guest_id"],
            )
        except (StoreForbidden, StoreNotFound, StoreConflict) as exc:
            raise _translate_store_error(exc) from exc
        return {"invite_code": invite_code}

    @app.post(
        "/api/v1/rooms/{room_id}/replace",
        dependencies=[Depends(_csrf)],
    )
    async def replace_room(
        room_id: str,
        body: RoomRequest,
        guest: dict[str, Any] = Depends(_guest),
        runtime: ServerRuntime = Depends(_runtime),
    ) -> dict[str, Any]:
        seed = body.seed if body.seed is not None else secrets.randbits(63)
        try:
            room, invite_code = runtime.store.replace_room(
                room_id,
                guest["guest_id"],
                seed=seed,
                player_count=body.player_count,
            )
        except (StoreForbidden, StoreNotFound, StoreConflict) as exc:
            raise _translate_store_error(exc) from exc
        return {"room": room, "invite_code": invite_code}

    @app.delete(
        "/api/v1/rooms/{room_id}/seats/{seat}",
        dependencies=[Depends(_csrf)],
    )
    async def remove_room_seat(
        room_id: str,
        seat: str,
        guest: dict[str, Any] = Depends(_guest),
        runtime: ServerRuntime = Depends(_runtime),
    ) -> dict[str, Any]:
        try:
            room = runtime.store.remove_seat(room_id, guest["guest_id"], seat)
        except (StoreForbidden, StoreNotFound, StoreConflict) as exc:
            raise _translate_store_error(exc) from exc
        return {"room": room}

    @app.delete(
        "/api/v1/rooms/{room_id}/membership",
        dependencies=[Depends(_csrf)],
    )
    async def leave_room(
        room_id: str,
        guest: dict[str, Any] = Depends(_guest),
        runtime: ServerRuntime = Depends(_runtime),
    ) -> dict[str, bool]:
        try:
            runtime.store.leave_room(room_id, guest["guest_id"])
        except (StoreForbidden, StoreNotFound, StoreConflict) as exc:
            raise _translate_store_error(exc) from exc
        return {"left": True}

    @app.put(
        "/api/v1/rooms/{room_id}/deck",
        dependencies=[Depends(_csrf)],
    )
    async def upload_deck(
        room_id: str,
        body: DeckRequest,
        request: Request,
        guest: dict[str, Any] = Depends(_guest),
        runtime: ServerRuntime = Depends(_runtime),
    ) -> dict[str, Any]:
        try:
            loader = DeckLoader(
                runtime.card_db,
                cache_dir=runtime.settings.game_root.parent / "deck-cache",
            )
            if body.source_url:
                if not is_moxfield_source(body.source_url.strip()):
                    raise ValueError("source_url must be a public Moxfield deck URL")
                deck = loader.load(
                    body.source_url.strip(),
                    commander=body.commander.strip() or None,
                    deck_name=body.name.strip(),
                )
            else:
                deck = parse_deck_text(
                    body.decklist,
                    name=body.name.strip(),
                    commander=body.commander.strip(),
                    source="browser-upload",
                )
                loader.resolve_names(deck)
            issues = loader.validate_commander_deck(deck, check_legality=False)
            legality_issues = loader.commander_legality_issues(deck)
            issues.extend(
                issue["message"]
                for issue in legality_issues
                if not issue["confirmable"]
            )
            if issues:
                raise StoreConflict("; ".join(issues))
            deck_fingerprint = deck_list_fingerprint(deck)
            legality_confirmation = None
            if legality_issues:
                legality_confirmation = _legality_confirmation_fingerprint(
                    deck_fingerprint,
                    legality_issues,
                )
                if body.legality_confirmation != legality_confirmation:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "legality_confirmation_required",
                            "message": (
                                "This list contains preview cards that are present "
                                "in Scryfall but are not yet Commander-legal."
                            ),
                            "confirmation": legality_confirmation,
                            "deck_list_fingerprint": deck_fingerprint,
                            "issues": legality_issues,
                        },
                    )
                deck.metadata["format_legality"] = {
                    "schema_version": 1,
                    "profile": "commander",
                    "status": "preview_override_confirmed",
                    "confirmation_fingerprint": legality_confirmation,
                    "issues": legality_issues,
                }
            preflight = semantic_preflight(runtime.card_db, deck)
            preflight["format_legality"] = deck.metadata.get(
                "format_legality",
                {
                    "schema_version": 1,
                    "profile": "commander",
                    "status": "legal",
                    "issues": [],
                },
            )
            saved = runtime.store.save_deck(
                guest["guest_id"],
                room_id,
                deck,
                fingerprint=deck_fingerprint,
                preflight=preflight,
            )
            oracle_ids = [
                runtime.card_db.lookup(name, fuzzy=False).oracle_id
                for name in deck.expanded()
            ]
            images: CardImageCache = request.app.state.images
            runtime.spawn(
                images.prefetch(oracle_ids),
                name=f"deck-image-prefetch-{room_id}",
            )
        except (StoreForbidden, StoreNotFound, StoreConflict) as exc:
            raise _translate_store_error(exc) from exc
        except (KeyError, ValueError, MoxfieldFetchError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "deck": saved,
            "preflight": _compact_preflight(preflight),
            "format_legality": preflight["format_legality"],
        }

    @app.delete(
        "/api/v1/rooms/{room_id}/deck",
        dependencies=[Depends(_csrf)],
    )
    async def clear_deck(
        room_id: str,
        guest: dict[str, Any] = Depends(_guest),
        runtime: ServerRuntime = Depends(_runtime),
    ) -> dict[str, Any]:
        try:
            room = runtime.store.clear_deck(guest["guest_id"], room_id)
        except (StoreForbidden, StoreNotFound, StoreConflict) as exc:
            raise _translate_store_error(exc) from exc
        return {"room": room}

    @app.post(
        "/api/v1/rooms/{room_id}/start",
        dependencies=[Depends(_csrf)],
    )
    async def start_game(
        room_id: str,
        guest: dict[str, Any] = Depends(_guest),
        runtime: ServerRuntime = Depends(_runtime),
    ) -> dict[str, Any]:
        try:
            seed, decks, profile = runtime.store.start_spec(
                room_id,
                guest["guest_id"],
            )
            if profile not in {"commander_duel", "commander_multiplayer"}:
                raise StoreConflict("Room has an unsupported format profile")
            with _capability_validation_snapshot():
                session = CommanderSession.create(
                    runtime.card_db,
                    decks,
                    first_player="A",
                    config=GameConfig(
                        seed=seed,
                        profile=profile,
                        review_profile="commander_review",
                        semantic_policy="trusted_only",
                        # Browser clients receive every empty priority capability.
                        # Their saved auto-pass preference may submit that ordinary
                        # command, while full-control users can hold priority.
                        auto_pass_empty_priority=False,
                        manual_active_main_phase=True,
                        # Browser tables expose a durable complete public log.
                        # Retain every engine event in the private Game Record;
                        # the public endpoint separately filters visibility and
                        # strips authoritative details.
                        trace_level="debug",
                    ),
                )
                service = _BrowserGameService(
                    session,
                    idempotency=runtime.idempotency,
                )
                runtime.records.save(service)
            runtime.store.commit_started_game(
                room_id,
                session.state.game_id,
                str(runtime.records.game_directory(session.state.game_id)),
                session.state.revision,
            )
            await runtime.manager.add(service, persistence=runtime.persistence)
        except (StoreForbidden, StoreNotFound, StoreConflict) as exc:
            raise _translate_store_error(exc) from exc
        return {
            "game_id": session.state.game_id,
            "room_id": room_id,
            "profile": profile,
            "state_revision": session.state.revision,
        }

    async def game_principal(
        game_id: str,
        guest: dict[str, Any],
        runtime: ServerRuntime,
    ) -> str:
        try:
            membership = runtime.store.game_membership(
                game_id, guest["guest_id"]
            )
        except (StoreForbidden, StoreNotFound) as exc:
            raise _translate_store_error(exc) from exc
        seat = membership["seat"]
        return "spectator" if membership["spectator"] else f"pilot:{seat}"

    @app.get("/api/v1/games/{game_id}/state")
    async def game_state(
        game_id: str,
        full: bool = False,
        guest: dict[str, Any] = Depends(_guest),
        runtime: ServerRuntime = Depends(_runtime),
    ) -> dict[str, Any]:
        principal = await game_principal(game_id, guest, runtime)
        try:
            actor = await runtime.actor(game_id)
            return {
                "packet": await actor.observe(principal, full=full),
                "game": await runtime.game_summary(
                    game_id, guest["guest_id"]
                ),
            }
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/games/{game_id}")
    async def inspect_game(
        game_id: str,
        guest: dict[str, Any] = Depends(_guest),
        runtime: ServerRuntime = Depends(_runtime),
    ) -> dict[str, Any]:
        return await _inspect_game_payload(
            runtime, game_id, guest["guest_id"]
        )

    _register_game_progress_route(app)

    @app.get("/api/v1/games/{game_id}/events")
    async def public_game_events(
        game_id: str,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=200),
        guest: dict[str, Any] = Depends(_guest),
        runtime: ServerRuntime = Depends(_runtime),
    ) -> dict[str, Any]:
        try:
            runtime.store.game_membership(game_id, guest["guest_id"])
            actor = await runtime.actor(game_id)
            return await actor.public_events(after=after, limit=limit)
        except (StoreForbidden, StoreNotFound) as exc:
            raise _translate_store_error(exc) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/games/{game_id}/stop",
        dependencies=[Depends(_csrf)],
    )
    async def stop_game(
        game_id: str,
        body: StopGameRequest,
        guest: dict[str, Any] = Depends(_guest),
        runtime: ServerRuntime = Depends(_runtime),
    ) -> dict[str, Any]:
        try:
            runtime.store.require_game_owner(game_id, guest["guest_id"])
            actor = await runtime.actor(game_id)
            result = await actor.pause(body.reason)
            if result["changed"]:
                await runtime.hub.notify(game_id)
            return {
                "game": await runtime.game_summary(
                    game_id, guest["guest_id"]
                )
            }
        except (StoreForbidden, StoreNotFound) as exc:
            raise _translate_store_error(exc) from exc
        except GameLifecycleConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/games/{game_id}/resume",
        dependencies=[Depends(_csrf)],
    )
    async def resume_game(
        game_id: str,
        body: ResumeGameRequest,
        guest: dict[str, Any] = Depends(_guest),
        runtime: ServerRuntime = Depends(_runtime),
    ) -> dict[str, Any]:
        try:
            runtime.store.require_game_owner(game_id, guest["guest_id"])
            actor = await runtime.actor(game_id)
            result = await actor.resume()
            if result["changed"]:
                await runtime.hub.notify(game_id)
            return {
                "game": await runtime.game_summary(
                    game_id, guest["guest_id"]
                )
            }
        except (StoreForbidden, StoreNotFound) as exc:
            raise _translate_store_error(exc) from exc
        except GameLifecycleConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/games/{game_id}/commands",
        dependencies=[Depends(_csrf)],
    )
    async def submit_command(
        game_id: str,
        request: Request,
        guest: dict[str, Any] = Depends(_guest),
        runtime: ServerRuntime = Depends(_runtime),
    ) -> dict[str, Any]:
        try:
            _, seat = runtime.store.game_access(game_id, guest["guest_id"])
        except (StoreForbidden, StoreNotFound) as exc:
            raise _translate_store_error(exc) from exc
        principal = f"pilot:{seat}"
        try:
            raw = await request.json()
            if not isinstance(raw, dict):
                raise ValueError("Command envelope must be an object")
            envelope = CommandEnvelope.from_mapping(raw)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        actor = await runtime.actor(game_id)
        receipt = await actor.command(principal, envelope)
        if receipt.ok and receipt.state_changed and not receipt.replayed:
            await runtime.hub.notify(game_id)
        return {"receipt": receipt.to_dict()}

    @app.websocket("/api/v1/games/{game_id}/stream")
    async def game_stream(websocket: WebSocket, game_id: str) -> None:
        runtime: ServerRuntime | None = websocket.app.state.runtime
        if runtime is None:
            await websocket.close(code=1013, reason="Card data is still being prepared")
            return
        origin = websocket.headers.get("origin")
        if not _websocket_origin_allowed(
            origin,
            websocket.headers.get("host", ""),
            resolved.allowed_origins,
        ):
            await websocket.close(code=1008, reason="Origin not allowed")
            return
        selected_protocol: str | None = None
        token = ""
        offered_tab_protocol = False
        offered_protocols = {
            value.strip()
            for value in websocket.headers.get("sec-websocket-protocol", "").split(",")
            if value.strip()
        }
        for protocol in offered_protocols:
            if not protocol.startswith(TAB_PROTOCOL_PREFIX):
                continue
            tab_id = protocol[len(TAB_PROTOCOL_PREFIX):].lower()
            if not TAB_ID_RE.fullmatch(tab_id):
                continue
            offered_tab_protocol = True
            token = websocket.cookies.get(_tab_cookie_name(tab_id)) or ""
            if token:
                selected_protocol = protocol
                break
        if not token and not offered_tab_protocol:
            token = websocket.cookies.get(COOKIE_NAME) or ""
        try:
            guest = runtime.store.authenticate(token)
            membership = runtime.store.game_membership(
                game_id, guest["guest_id"]
            )
            actor = await runtime.actor(game_id)
        except (StoreForbidden, StoreNotFound, KeyError, ValueError):
            # Accept once so browser clients receive a terminal protocol
            # message. Rejecting the HTTP upgrade as 403 gives JavaScript no
            # useful status and previously caused an endless reconnect loop
            # for stale game tabs after a local server reset.
            await websocket.accept(subprotocol=selected_protocol)
            await websocket.send_json(
                {
                    "type": "terminal",
                    "code": "game_access_lost",
                    "message": (
                        "This browser tab no longer has access to that game. "
                        "Return to the lobby and open the current room."
                    ),
                }
            )
            await websocket.close(code=4401, reason="Game access lost")
            return
        principal = (
            "spectator"
            if membership["spectator"]
            else f"pilot:{membership['seat']}"
        )
        cursor_key = f"network:{principal}:{uuid.uuid4().hex}"
        await websocket.accept(subprotocol=selected_protocol)
        version = runtime.hub.version(game_id)
        try:
            packet = await actor.observe(
                principal, full=True, cursor_key=cursor_key
            )
            await websocket.send_json(
                {
                    "type": "projection",
                    "packet": packet,
                    "game": await runtime.game_summary(
                        game_id, guest["guest_id"]
                    ),
                }
            )
            receive_task = asyncio.create_task(websocket.receive())
            while True:
                hub_task = asyncio.create_task(
                    runtime.hub.wait(game_id, version)
                )
                try:
                    done, _ = await asyncio.wait(
                        {receive_task, hub_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if receive_task in done:
                        hub_task.cancel()
                        await asyncio.gather(hub_task, return_exceptions=True)
                        message = receive_task.result()
                        if message["type"] == "websocket.disconnect":
                            return
                        receive_task = asyncio.create_task(
                            websocket.receive()
                        )
                        continue
                    version = hub_task.result()
                    packet = await actor.observe(
                        principal, cursor_key=cursor_key
                    )
                    await websocket.send_json(
                        {
                            "type": "projection",
                            "packet": packet,
                            "game": await runtime.game_summary(
                                game_id, guest["guest_id"]
                            ),
                        },
                    )
                except TimeoutError:
                    await websocket.send_json(
                        {"type": "ping", "view_revision": packet["view_revision"]}
                    )
        except WebSocketDisconnect:
            return
        finally:
            tasks = [
                task
                for task in (
                    locals().get("receive_task"),
                    locals().get("hub_task"),
                )
                if isinstance(task, asyncio.Task)
            ]
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await actor.drop_projection_cursor(cursor_key)

    index_path = resolved.static_dir / "index.html"
    assets_path = resolved.static_dir / "assets"
    if index_path.is_file():
        if assets_path.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_path), name="browser-assets")

        @app.get("/{browser_path:path}", include_in_schema=False)
        async def browser_application(browser_path: str) -> FileResponse:
            if browser_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Unknown API route")
            root = resolved.static_dir.resolve()
            candidate = (root / browser_path).resolve()
            if candidate != root and root in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index_path)

    return app
