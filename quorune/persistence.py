from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
from threading import Lock
from typing import Any

from .record_trust import runtime_trust_provenance

from .carddb import CardDatabase
from .service import (
    CommandReceipt,
    GameService,
    IdempotencyRecord,
    IdempotencyRepository,
)
from .session import CommanderSession


GAME_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")


SQLITE_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS server_schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS guest_sessions (
    guest_id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS rooms (
    room_id TEXT PRIMARY KEY,
    owner_guest_id TEXT NOT NULL,
    invite_code_hash TEXT NOT NULL UNIQUE,
    visibility TEXT NOT NULL,
    status TEXT NOT NULL,
    seat_count INTEGER NOT NULL,
    format_profile TEXT NOT NULL,
    seed INTEGER NOT NULL,
    game_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(owner_guest_id) REFERENCES guest_sessions(guest_id)
);
CREATE TABLE IF NOT EXISTS room_members (
    room_id TEXT NOT NULL,
    guest_id TEXT NOT NULL,
    spectator INTEGER NOT NULL DEFAULT 0,
    joined_at TEXT NOT NULL,
    PRIMARY KEY(room_id, guest_id),
    FOREIGN KEY(room_id) REFERENCES rooms(room_id) ON DELETE CASCADE,
    FOREIGN KEY(guest_id) REFERENCES guest_sessions(guest_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS decks (
    deck_id TEXT PRIMARY KEY,
    owner_guest_id TEXT NOT NULL,
    name TEXT NOT NULL,
    deck_list_fingerprint TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    preflight_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(owner_guest_id) REFERENCES guest_sessions(guest_id)
);
CREATE TABLE IF NOT EXISTS room_seats (
    room_id TEXT NOT NULL,
    seat TEXT NOT NULL,
    guest_id TEXT,
    deck_id TEXT,
    ready INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(room_id, seat),
    UNIQUE(room_id, guest_id),
    FOREIGN KEY(room_id) REFERENCES rooms(room_id) ON DELETE CASCADE,
    FOREIGN KEY(guest_id) REFERENCES guest_sessions(guest_id),
    FOREIGN KEY(deck_id) REFERENCES decks(deck_id)
);
CREATE TABLE IF NOT EXISTS games (
    game_id TEXT PRIMARY KEY,
    room_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    record_path TEXT NOT NULL,
    state_revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(room_id) REFERENCES rooms(room_id)
);
CREATE TABLE IF NOT EXISTS idempotency_records (
    game_id TEXT NOT NULL,
    principal TEXT NOT NULL,
    command_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (game_id, principal, command_id)
);
"""


def initialize_sqlite(path: str | Path) -> None:
    database = Path(path)
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        connection.executescript(SQLITE_SCHEMA)
        connection.execute(
            "INSERT OR IGNORE INTO server_schema_migrations(version) VALUES (1)"
        )
        connection.commit()
    finally:
        connection.close()


class SqliteIdempotencyRepository(IdempotencyRepository):
    """Durable idempotency adapter containing no bearer capabilities."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        initialize_sqlite(self.path)

    def get(
        self, game_id: str, principal: str, command_id: str
    ) -> IdempotencyRecord | None:
        connection = sqlite3.connect(self.path)
        try:
            row = connection.execute(
                """
                SELECT request_fingerprint, receipt_json
                FROM idempotency_records
                WHERE game_id = ? AND principal = ? AND command_id = ?
                """,
                (game_id, principal, command_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        receipt = json.loads(str(row[1]))
        if not isinstance(receipt, dict):
            raise ValueError("Stored idempotency receipt is malformed")
        return IdempotencyRecord(
            request_fingerprint=str(row[0]),
            receipt=CommandReceipt.from_dict(receipt),
        )

    def put(
        self,
        game_id: str,
        principal: str,
        command_id: str,
        record: IdempotencyRecord,
    ) -> None:
        receipt_json = json.dumps(
            record.receipt.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT request_fingerprint, receipt_json
                FROM idempotency_records
                WHERE game_id = ? AND principal = ? AND command_id = ?
                """,
                (game_id, principal, command_id),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO idempotency_records(
                        game_id, principal, command_id,
                        request_fingerprint, receipt_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        game_id,
                        principal,
                        command_id,
                        record.request_fingerprint,
                        receipt_json,
                    ),
                )
            elif (
                str(existing[0]) != record.request_fingerprint
                or str(existing[1]) != receipt_json
            ):
                raise RuntimeError("Idempotency record changed after commit")
            connection.commit()
        finally:
            connection.close()


class DirectoryGamePersistence:
    """Durable Game Record v3 adapter for a single-node server.

    Each game is stored below a fixed server-owned root.  The repository never
    accepts a filesystem path from a client, and a game ID must pass the public
    identifier grammar before it is resolved.  Live command acknowledgements
    persist the replayable core only; derived review artifacts are generated
    for terminal or paused records and by explicit direct/finalization saves.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._trust_lock = Lock()
        self._runtime_trust: dict[str, Any] | None = None

    def game_directory(self, game_id: str) -> Path:
        if not GAME_ID_RE.fullmatch(game_id):
            raise ValueError("Invalid game id")
        target = (self.root / game_id).resolve()
        if target.parent != self.root:
            raise ValueError("Game path escaped the persistence root")
        return target

    def save(self, service: GameService) -> dict[str, float]:
        session = service.session
        # Server deployments use an immutable code revision for the lifetime of the writer.
        with self._trust_lock:
            if self._runtime_trust is None:
                self._runtime_trust = runtime_trust_provenance()
            trust = self._runtime_trust
        return service.session.save(
            self.game_directory(session.state.game_id),
            runtime_trust_snapshot=trust,
            include_review=(
                session.state.game_over
                or session.record_status not in {"created", "in_progress"}
            ),
        )

    def exists(self, game_id: str) -> bool:
        return (self.game_directory(game_id) / "manifest.json").is_file()

    def load(
        self,
        card_db: CardDatabase,
        game_id: str,
        *,
        idempotency: IdempotencyRepository | None = None,
    ) -> GameService:
        directory = self.game_directory(game_id)
        if not (directory / "manifest.json").is_file():
            raise KeyError(f"Unknown persisted game {game_id}")
        session = CommanderSession.load(card_db, directory)
        if session.state.game_id != game_id:
            raise ValueError("Persisted game id does not match its directory")
        return GameService(session, idempotency=idempotency)

    def game_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                path.name
                for path in self.root.iterdir()
                if path.is_dir()
                and GAME_ID_RE.fullmatch(path.name)
                and (path / "manifest.json").is_file()
            )
        )
