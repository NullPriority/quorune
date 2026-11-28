from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Callable, Iterator
import uuid

from quorune import DeckDefinition
from quorune.persistence import initialize_sqlite

from .deck_readiness import compact_deck_preflight, has_integrity_blocker


SEATS = ("A", "B", "C", "D")
GAME_STATUSES = {"active", "paused", "complete", "aborted"}


@dataclass(frozen=True, slots=True)
class RoomStartSpec:
    seed: int
    profile: str
    decks: dict[str, DeckDefinition]
    roster: tuple[tuple[str, str, str, int], ...]


class StoreConflict(ValueError):
    pass


class StoreNotFound(KeyError):
    pass


class StoreForbidden(PermissionError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ServerStore:
    """Small SQLite control-plane store; game state stays in Game Record v3."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        initialize_sqlite(self.path)

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except BaseException:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    def create_guest(
        self, display_name: str, *, ttl: timedelta = timedelta(days=7)
    ) -> tuple[dict[str, Any], str]:
        guest_id = uuid.uuid4().hex
        token = secrets.token_urlsafe(32)
        created_at = datetime.now(timezone.utc)
        expires_at = created_at + ttl
        with self._connection(write=True) as connection:
            connection.execute(
                """
                INSERT INTO guest_sessions(
                    guest_id, token_hash, display_name, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    guest_id,
                    token_hash(token),
                    display_name,
                    created_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )
        return {
            "guest_id": guest_id,
            "display_name": display_name,
            "expires_at": expires_at.isoformat(),
        }, token

    def authenticate(self, token: str) -> dict[str, Any]:
        if not token:
            raise StoreForbidden("Missing guest session")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT guest_id, display_name, expires_at, revoked_at
                FROM guest_sessions WHERE token_hash = ?
                """,
                (token_hash(token),),
            ).fetchone()
        if row is None or row["revoked_at"] is not None:
            raise StoreForbidden("Invalid guest session")
        if datetime.fromisoformat(str(row["expires_at"])) <= datetime.now(
            timezone.utc
        ):
            raise StoreForbidden("Guest session expired")
        return {
            "guest_id": str(row["guest_id"]),
            "display_name": str(row["display_name"]),
            "expires_at": str(row["expires_at"]),
        }

    @staticmethod
    def _insert_room(
        connection: sqlite3.Connection,
        owner_guest_id: str,
        *,
        seed: int,
        player_count: int,
        room_id: str | None = None,
    ) -> tuple[str, str]:
        if player_count not in {2, 4}:
            raise StoreConflict("A browser room must have capacity for 2 or 4 seats")
        room_id = room_id or uuid.uuid4().hex
        invite_code = secrets.token_urlsafe(18)
        now = utc_now()
        profile = "commander_duel" if player_count == 2 else "commander_multiplayer"
        connection.execute(
            """
            INSERT INTO rooms(
                room_id, owner_guest_id, invite_code_hash, visibility,
                status, seat_count, format_profile, seed, created_at,
                updated_at
            ) VALUES (?, ?, ?, 'invite', 'open', ?, ?, ?, ?, ?)
            """,
            (
                room_id,
                owner_guest_id,
                token_hash(invite_code),
                player_count,
                profile,
                seed,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO room_members(room_id, guest_id, joined_at)
            VALUES (?, ?, ?)
            """,
            (room_id, owner_guest_id, now),
        )
        connection.executemany(
            "INSERT INTO room_seats(room_id, seat) VALUES (?, ?)",
            [(room_id, seat) for seat in SEATS[:player_count]],
        )
        connection.execute(
            "UPDATE room_seats SET guest_id = ? WHERE room_id = ? AND seat = 'A'",
            (owner_guest_id, room_id),
        )
        return room_id, invite_code

    def create_room(
        self, owner_guest_id: str, *, seed: int, player_count: int = 4
    ) -> tuple[dict[str, Any], str]:
        with self._connection(write=True) as connection:
            room_id, invite_code = self._insert_room(
                connection,
                owner_guest_id,
                seed=seed,
                player_count=player_count,
            )
        return self.room(room_id, owner_guest_id), invite_code

    def replace_room(
        self,
        room_id: str,
        owner_guest_id: str,
        *,
        seed: int,
        player_count: int,
    ) -> tuple[dict[str, Any], str]:
        with self._connection(write=True) as connection:
            room = connection.execute(
                "SELECT owner_guest_id, status FROM rooms WHERE room_id = ?",
                (room_id,),
            ).fetchone()
            if room is None:
                raise StoreNotFound("Room not found")
            if room["owner_guest_id"] != owner_guest_id:
                raise StoreForbidden("Only the room owner can replace the room")
            if room["status"] != "open":
                raise StoreConflict("Only an unstarted room can be replaced")
            connection.execute(
                "UPDATE room_seats SET deck_id = NULL, ready = 0 WHERE room_id = ?",
                (room_id,),
            )
            connection.execute(
                """
                UPDATE rooms
                SET status = 'closed', invite_code_hash = ?, updated_at = ?
                WHERE room_id = ?
                """,
                (token_hash(secrets.token_urlsafe(32)), utc_now(), room_id),
            )
            next_room_id, invite_code = self._insert_room(
                connection,
                owner_guest_id,
                seed=seed,
                player_count=player_count,
            )
        return self.room(next_room_id, owner_guest_id), invite_code

    def join_room(
        self, guest_id: str, *, invite_code: str, seat: str
    ) -> dict[str, Any]:
        seat = seat.upper()
        if seat not in SEATS:
            raise StoreConflict("Seat must be A, B, C, or D")
        now = utc_now()
        with self._connection(write=True) as connection:
            room = connection.execute(
                "SELECT room_id, status FROM rooms WHERE invite_code_hash = ?",
                (token_hash(invite_code),),
            ).fetchone()
            if room is None:
                raise StoreNotFound("Invite code not found")
            if room["status"] != "open":
                raise StoreConflict("Room is no longer open")
            room_id = str(room["room_id"])
            occupied = connection.execute(
                "SELECT guest_id FROM room_seats WHERE room_id = ? AND seat = ?",
                (room_id, seat),
            ).fetchone()
            if occupied is None:
                raise StoreNotFound("Seat not found")
            if occupied["guest_id"] not in (None, guest_id):
                raise StoreConflict(f"Seat {seat} is occupied")
            existing = connection.execute(
                "SELECT seat FROM room_seats WHERE room_id = ? AND guest_id = ?",
                (room_id, guest_id),
            ).fetchone()
            if existing is not None and existing["seat"] != seat:
                raise StoreConflict("Guest already occupies another seat")
            connection.execute(
                """
                INSERT OR IGNORE INTO room_members(room_id, guest_id, joined_at)
                VALUES (?, ?, ?)
                """,
                (room_id, guest_id, now),
            )
            connection.execute(
                "UPDATE room_members SET spectator = 0 WHERE room_id = ? AND guest_id = ?",
                (room_id, guest_id),
            )
            connection.execute(
                "UPDATE room_seats SET guest_id = ? WHERE room_id = ? AND seat = ?",
                (guest_id, room_id, seat),
            )
            connection.execute(
                "UPDATE rooms SET updated_at = ? WHERE room_id = ?",
                (now, room_id),
            )
        return self.room(room_id, guest_id)

    def join_spectator(
        self, guest_id: str, *, invite_code: str
    ) -> dict[str, Any]:
        """Join an invited room without claiming a player seat."""

        now = utc_now()
        with self._connection(write=True) as connection:
            room = connection.execute(
                "SELECT room_id, status FROM rooms WHERE invite_code_hash = ?",
                (token_hash(invite_code),),
            ).fetchone()
            if room is None:
                raise StoreNotFound("Invite code not found")
            if room["status"] == "closed":
                raise StoreConflict("Room is closed")
            room_id = str(room["room_id"])
            seated = connection.execute(
                "SELECT 1 FROM room_seats WHERE room_id = ? AND guest_id = ?",
                (room_id, guest_id),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO room_members(room_id, guest_id, spectator, joined_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(room_id, guest_id) DO UPDATE SET
                    spectator = excluded.spectator
                """,
                (room_id, guest_id, 0 if seated is not None else 1, now),
            )
            connection.execute(
                "UPDATE rooms SET updated_at = ? WHERE room_id = ?",
                (now, room_id),
            )
        return self.room(room_id, guest_id)

    def rotate_invite(self, room_id: str, guest_id: str) -> str:
        invite_code = secrets.token_urlsafe(18)
        with self._connection(write=True) as connection:
            room = connection.execute(
                "SELECT owner_guest_id, status FROM rooms WHERE room_id = ?",
                (room_id,),
            ).fetchone()
            if room is None:
                raise StoreNotFound("Room not found")
            if room["owner_guest_id"] != guest_id:
                raise StoreForbidden("Only the room owner can replace the invite code")
            if room["status"] != "open":
                raise StoreConflict("Invite codes cannot change after game start")
            connection.execute(
                "UPDATE rooms SET invite_code_hash = ?, updated_at = ? WHERE room_id = ?",
                (token_hash(invite_code), utc_now(), room_id),
            )
        return invite_code

    def remove_seat(self, room_id: str, owner_guest_id: str, seat: str) -> dict[str, Any]:
        seat = seat.upper()
        with self._connection(write=True) as connection:
            room = connection.execute(
                "SELECT owner_guest_id, status FROM rooms WHERE room_id = ?",
                (room_id,),
            ).fetchone()
            if room is None:
                raise StoreNotFound("Room not found")
            if room["owner_guest_id"] != owner_guest_id:
                raise StoreForbidden("Only the room owner can remove a player")
            if room["status"] != "open":
                raise StoreConflict("Players cannot be removed after game start")
            if seat == "A":
                raise StoreForbidden("The room owner cannot remove their own seat")
            target = connection.execute(
                "SELECT guest_id, deck_id FROM room_seats WHERE room_id = ? AND seat = ?",
                (room_id, seat),
            ).fetchone()
            if target is None:
                raise StoreNotFound("Seat not found")
            if target["guest_id"] is None:
                raise StoreConflict(f"Seat {seat} is already open")
            connection.execute(
                "UPDATE room_seats SET guest_id = NULL, deck_id = NULL, ready = 0 WHERE room_id = ? AND seat = ?",
                (room_id, seat),
            )
            connection.execute(
                "DELETE FROM room_members WHERE room_id = ? AND guest_id = ?",
                (room_id, target["guest_id"]),
            )
            connection.execute(
                "UPDATE rooms SET updated_at = ? WHERE room_id = ?",
                (utc_now(), room_id),
            )
        return self.room(room_id, owner_guest_id)

    def leave_room(self, room_id: str, guest_id: str) -> None:
        with self._connection(write=True) as connection:
            room = connection.execute(
                "SELECT owner_guest_id, status FROM rooms WHERE room_id = ?",
                (room_id,),
            ).fetchone()
            if room is None:
                raise StoreNotFound("Room not found")
            member = connection.execute(
                "SELECT spectator FROM room_members WHERE room_id = ? AND guest_id = ?",
                (room_id, guest_id),
            ).fetchone()
            if member is None:
                raise StoreForbidden("Guest is not a member of this room")
            if bool(member["spectator"]):
                connection.execute(
                    "DELETE FROM room_members WHERE room_id = ? AND guest_id = ?",
                    (room_id, guest_id),
                )
                connection.execute(
                    "UPDATE rooms SET updated_at = ? WHERE room_id = ?",
                    (utc_now(), room_id),
                )
                return
            if room["owner_guest_id"] == guest_id:
                raise StoreForbidden("The owner must replace the room instead")
            if room["status"] != "open":
                raise StoreConflict("A player cannot leave after game start")
            target = connection.execute(
                "SELECT deck_id FROM room_seats WHERE room_id = ? AND guest_id = ?",
                (room_id, guest_id),
            ).fetchone()
            if target is None:
                raise StoreForbidden("Guest does not occupy a seat in this room")
            connection.execute(
                "UPDATE room_seats SET guest_id = NULL, deck_id = NULL, ready = 0 WHERE room_id = ? AND guest_id = ?",
                (room_id, guest_id),
            )
            connection.execute(
                "DELETE FROM room_members WHERE room_id = ? AND guest_id = ?",
                (room_id, guest_id),
            )
            connection.execute(
                "UPDATE rooms SET updated_at = ? WHERE room_id = ?",
                (utc_now(), room_id),
            )

    def room(self, room_id: str, guest_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            member = connection.execute(
                "SELECT spectator FROM room_members WHERE room_id = ? AND guest_id = ?",
                (room_id, guest_id),
            ).fetchone()
            if member is None:
                raise StoreForbidden("Guest is not a room member")
            room = connection.execute(
                "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
            ).fetchone()
            if room is None:
                raise StoreNotFound("Room not found")
            seats = connection.execute(
                """
                SELECT s.seat, s.ready, s.guest_id, g.display_name,
                       d.deck_id, d.name AS deck_name,
                       d.deck_list_fingerprint, d.preflight_json
                FROM room_seats s
                LEFT JOIN guest_sessions g ON g.guest_id = s.guest_id
                LEFT JOIN decks d ON d.deck_id = s.deck_id
                WHERE s.room_id = ? ORDER BY s.seat
                """,
                (room_id,),
            ).fetchall()
        spectator = bool(member["spectator"])
        seat_payloads: list[dict[str, Any]] = []
        for row in seats:
            mine = not spectator and row["guest_id"] == guest_id
            deck_payload = None
            if row["deck_id"] is not None:
                try:
                    preflight = json.loads(str(row["preflight_json"] or "{}"))
                except json.JSONDecodeError:
                    preflight = {}
                format_legality = preflight.get("format_legality") or {}
                legality_issues = format_legality.get("issues") or []
                deck_payload = {
                    "deck_id": row["deck_id"],
                    "name": row["deck_name"],
                    "deck_list_fingerprint": row["deck_list_fingerprint"],
                    "format_legality": {
                        "status": str(format_legality.get("status") or "unknown"),
                        "issue_count": len(legality_issues),
                        # Card names in an unrevealed deck remain visible only
                        # to its owner; other room members receive the public
                        # override state and count.
                        "issues": legality_issues if mine else [],
                    },
                }
                if mine and "trusted_only_ready" in preflight:
                    deck_payload["preflight"] = compact_deck_preflight(preflight)
            seat_payloads.append(
                {
                    "seat": str(row["seat"]),
                    "guest_id": row["guest_id"],
                    "display_name": row["display_name"],
                    "ready": bool(row["ready"]),
                    "deck": deck_payload,
                    "mine": mine,
                }
            )
        return {
            "room_id": room_id,
            "owner_guest_id": str(room["owner_guest_id"]),
            "status": str(room["status"]),
            "format_profile": str(room["format_profile"]),
            "seat_count": int(room["seat_count"]),
            "game_id": room["game_id"],
            "spectator": spectator,
            "seats": seat_payloads,
        }

    def guest_seat(self, room_id: str, guest_id: str) -> str:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT seat FROM room_seats WHERE room_id = ? AND guest_id = ?",
                (room_id, guest_id),
            ).fetchone()
        if row is None:
            raise StoreForbidden("Guest does not occupy a seat in this room")
        return str(row["seat"])

    def saved_decks(self, guest_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT deck_id, name, definition_json FROM decks
                WHERE owner_guest_id = ?
                GROUP BY definition_json
                HAVING created_at = MAX(created_at)
                ORDER BY created_at DESC LIMIT 16
                """,
                (guest_id,),
            ).fetchall()
        return [
            {"id": row["deck_id"], "name": row["name"],
             "commanders": json.loads(row["definition_json"]).get("commanders", [])}
            for row in rows
        ]

    def saved_deck(self, guest_id: str, deck_id: str) -> DeckDefinition:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT definition_json FROM decks WHERE deck_id = ? AND owner_guest_id = ?",
                (deck_id, guest_id),
            ).fetchone()
        if row is None:
            raise StoreNotFound("Saved deck not found")
        return DeckDefinition.from_dict(json.loads(row["definition_json"]))

    def recent_games(self, guest_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT g.game_id, g.room_id, g.status, g.created_at, r.format_profile
                FROM games g
                JOIN rooms r ON r.room_id = g.room_id
                JOIN room_members m ON m.room_id = g.room_id AND m.guest_id = ?
                LEFT JOIN room_seats s ON s.room_id = g.room_id AND s.guest_id = m.guest_id
                WHERE m.spectator = 1 OR s.seat IS NOT NULL
                ORDER BY g.updated_at DESC, g.game_id LIMIT 12
                """,
                (guest_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def rematch(self, game_id: str, guest_id: str, *, seed: int) -> tuple[dict[str, Any], str]:
        """Prepare a fresh room without changing access to the completed record."""
        next_room = uuid.uuid5(uuid.NAMESPACE_URL, f"quorune:rematch:{game_id}").hex
        invite_code = ""
        with self._connection(write=True) as connection:
            original = connection.execute(
                """
                SELECT g.room_id, g.status, r.owner_guest_id, r.seat_count
                FROM games g JOIN rooms r ON r.room_id = g.room_id
                JOIN room_seats s ON s.room_id = g.room_id AND s.guest_id = ?
                WHERE g.game_id = ?
                """,
                (guest_id, game_id),
            ).fetchone()
            if original is None:
                raise StoreNotFound("Game not found for this player")
            if original["status"] != "complete":
                raise StoreConflict("A rematch can be set up after the game ends")
            existing = connection.execute(
                "SELECT status FROM rooms WHERE room_id = ?", (next_room,),
            ).fetchone()
            if existing is None:
                if original["owner_guest_id"] != guest_id:
                    raise StoreConflict("The host hasn't set up the next table yet")
                _, invite_code = self._insert_room(
                    connection, guest_id, seed=seed, player_count=original["seat_count"], room_id=next_room,
                )
                self._carry_rematch_seats(connection, original["room_id"], next_room)
            elif existing["status"] == "closed":
                raise StoreConflict("The next table was closed. Create a fresh table from the lobby.")
        return self.room(next_room, guest_id), invite_code

    @staticmethod
    def _carry_rematch_seats(connection: sqlite3.Connection, previous: str, next_room: str) -> None:
        now = utc_now()
        rows = connection.execute(
            """
            SELECT s.seat, s.guest_id, s.ready, d.deck_id
            FROM room_seats s JOIN guest_sessions g ON g.guest_id = s.guest_id
            LEFT JOIN decks d ON d.deck_id = s.deck_id AND d.owner_guest_id = s.guest_id
            WHERE s.room_id = ? AND g.revoked_at IS NULL AND g.expires_at > ?
            """,
            (previous, now),
        ).fetchall()
        connection.executemany(
            "INSERT OR IGNORE INTO room_members(room_id, guest_id, joined_at) VALUES (?, ?, ?)",
            [(next_room, row["guest_id"], now) for row in rows],
        )
        connection.executemany(
            "UPDATE room_seats SET guest_id = ?, deck_id = ?, ready = ? WHERE room_id = ? AND seat = ?",
            [(row["guest_id"], row["deck_id"], int(bool(row["deck_id"] and row["ready"])), next_room, row["seat"])
             for row in rows],
        )

    def save_deck(
        self,
        guest_id: str,
        room_id: str,
        deck: DeckDefinition,
        *,
        fingerprint: str,
        preflight: dict[str, Any],
    ) -> dict[str, Any]:
        deck_id = uuid.uuid4().hex
        now = utc_now()
        with self._connection(write=True) as connection:
            room = connection.execute(
                "SELECT status FROM rooms WHERE room_id = ?", (room_id,)
            ).fetchone()
            if room is None:
                raise StoreNotFound("Room not found")
            if room["status"] != "open":
                raise StoreConflict("Decks cannot change after game start")
            seat = connection.execute(
                "SELECT seat FROM room_seats WHERE room_id = ? AND guest_id = ?",
                (room_id, guest_id),
            ).fetchone()
            if seat is None:
                raise StoreForbidden("Guest does not occupy a room seat")
            connection.execute(
                """
                INSERT INTO decks(
                    deck_id, owner_guest_id, name, deck_list_fingerprint,
                    definition_json, preflight_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    deck_id,
                    guest_id,
                    deck.name,
                    fingerprint,
                    json.dumps(deck.to_dict(), sort_keys=True),
                    json.dumps(preflight, sort_keys=True),
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE room_seats SET deck_id = ?, ready = ?
                WHERE room_id = ? AND guest_id = ?
                """,
                (deck_id, int(not has_integrity_blocker(preflight)), room_id, guest_id),
            )
            connection.execute(
                "UPDATE rooms SET updated_at = ? WHERE room_id = ?",
                (now, room_id),
            )
        return {
            "deck_id": deck_id,
            "name": deck.name,
            "commanders": list(deck.commanders),
            "deck_list_fingerprint": fingerprint,
            "total_cards": deck.total_cards(),
        }

    def clear_deck(self, guest_id: str, room_id: str) -> dict[str, Any]:
        with self._connection(write=True) as connection:
            room = connection.execute(
                "SELECT status FROM rooms WHERE room_id = ?", (room_id,)
            ).fetchone()
            if room is None:
                raise StoreNotFound("Room not found")
            if room["status"] != "open":
                raise StoreConflict("Deck readiness cannot change after game start")
            seat = connection.execute(
                "SELECT seat, deck_id FROM room_seats WHERE room_id = ? AND guest_id = ?",
                (room_id, guest_id),
            ).fetchone()
            if seat is None:
                raise StoreForbidden("Guest does not occupy a room seat")
            connection.execute(
                "UPDATE room_seats SET deck_id = NULL, ready = 0 WHERE room_id = ? AND guest_id = ?",
                (room_id, guest_id),
            )
            connection.execute(
                "UPDATE rooms SET updated_at = ? WHERE room_id = ?",
                (utc_now(), room_id),
            )
        return self.room(room_id, guest_id)

    def start_spec(
        self, room_id: str, owner_guest_id: str, *, use_reviewed_card_data: bool = False,
    ) -> RoomStartSpec:
        with self._connection() as connection:
            room = connection.execute(
                "SELECT * FROM rooms WHERE room_id = ?", (room_id,)
            ).fetchone()
            if room is None:
                raise StoreNotFound("Room not found")
            if room["owner_guest_id"] != owner_guest_id:
                raise StoreForbidden("Only the room owner can start the game")
            if room["status"] != "open":
                raise StoreConflict("Room has already started")
            rows = connection.execute(
                """
                SELECT s.seat, s.guest_id, s.ready, d.deck_id, d.definition_json,
                       d.preflight_json
                FROM room_seats s
                LEFT JOIN decks d ON d.deck_id = s.deck_id
                WHERE s.room_id = ? ORDER BY s.seat
                """,
                (room_id,),
            ).fetchall()
        seat_count = int(room["seat_count"])
        minimum_players = 2 if seat_count == 2 else 3
        occupied = [row for row in rows if row["guest_id"] is not None]
        if len(occupied) < minimum_players:
            raise StoreConflict(f"At least {minimum_players} players must join before the game can start")
        if any(row["definition_json"] is None for row in occupied):
            raise StoreConflict("Every seated player must submit a deck")
        waiting = [row for row in occupied if not row["ready"]]
        if use_reviewed_card_data:
            if not waiting or not all(
                has_integrity_blocker(json.loads(row["preflight_json"] or "{}"))
                for row in waiting
            ):
                raise StoreConflict("Earlier card data is available only for decks waiting for a card data review")
        elif waiting:
            raise StoreConflict("Every seated player's deck must be ready")
        return RoomStartSpec(
            seed=int(room["seed"]),
            profile=str(room["format_profile"]),
            decks={
                str(row["seat"]): DeckDefinition.from_dict(json.loads(row["definition_json"]))
                for row in occupied
            },
            roster=tuple(
                (row["seat"], row["guest_id"], row["deck_id"], row["ready"])
                for row in occupied
            ),
        )

    def commit_started_game(
        self, room_id: str, game_id: str, record_path: str, revision: int, *,
        expected_roster: tuple[tuple[str, str, str, int], ...], save_record: Callable[[], object],
    ) -> None:
        now = utc_now()
        with self._connection(write=True) as connection:
            room = connection.execute(
                "SELECT status FROM rooms WHERE room_id = ?", (room_id,)
            ).fetchone()
            if room is None:
                raise StoreNotFound("Room not found")
            if room["status"] != "open":
                raise StoreConflict("Room has already started")
            roster = connection.execute(
                """
                SELECT seat, guest_id, deck_id, ready FROM room_seats
                WHERE room_id = ? AND guest_id IS NOT NULL ORDER BY seat
                """,
                (room_id,),
            ).fetchall()
            if tuple(map(tuple, roster)) != expected_roster:
                raise StoreConflict("The roster or a deck changed while the game was starting. Review the table and try again.")
            save_record()
            connection.execute(
                """
                INSERT INTO games(
                    game_id, room_id, status, record_path, state_revision,
                    created_at, updated_at
                ) VALUES (?, ?, 'active', ?, ?, ?, ?)
                """,
                (game_id, room_id, record_path, revision, now, now),
            )
            connection.execute(
                """
                UPDATE rooms SET status = 'active', game_id = ?, updated_at = ?
                WHERE room_id = ?
                """,
                (game_id, now, room_id),
            )

    def game_access(self, game_id: str, guest_id: str) -> tuple[str, str]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT g.room_id, s.seat
                FROM games g
                JOIN room_seats s ON s.room_id = g.room_id
                WHERE g.game_id = ? AND s.guest_id = ?
                """,
                (game_id, guest_id),
            ).fetchone()
        if row is None:
            raise StoreForbidden("Guest cannot access this game")
        return str(row["room_id"]), str(row["seat"])

    def game_membership(self, game_id: str, guest_id: str) -> dict[str, Any]:
        """Return a game member's projection role without granting seat authority."""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT g.room_id, m.spectator, s.seat
                FROM games g
                JOIN room_members m ON m.room_id = g.room_id
                LEFT JOIN room_seats s
                  ON s.room_id = g.room_id AND s.guest_id = m.guest_id
                WHERE g.game_id = ? AND m.guest_id = ?
                """,
                (game_id, guest_id),
            ).fetchone()
        if row is None:
            raise StoreForbidden("Guest cannot access this game")
        spectator = bool(row["spectator"])
        seat = None if spectator or row["seat"] is None else str(row["seat"])
        if not spectator and seat is None:
            raise StoreForbidden("Guest cannot access this game")
        return {
            "room_id": str(row["room_id"]),
            "seat": seat,
            "spectator": spectator,
            "role": "spectator" if spectator else "player",
        }

    def game_summary(self, game_id: str, guest_id: str) -> dict[str, Any]:
        """Return control-plane metadata safe for a player or spectator."""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT g.game_id, g.room_id, g.status, g.state_revision,
                       g.created_at, g.updated_at, r.owner_guest_id,
                       r.format_profile, m.spectator, s.seat
                FROM games g
                JOIN rooms r ON r.room_id = g.room_id
                JOIN room_members m ON m.room_id = g.room_id
                LEFT JOIN room_seats s
                  ON s.room_id = g.room_id AND s.guest_id = m.guest_id
                WHERE g.game_id = ? AND m.guest_id = ?
                """,
                (game_id, guest_id),
            ).fetchone()
        if row is None:
            raise StoreForbidden("Guest cannot access this game")
        spectator = bool(row["spectator"])
        seat = None if spectator or row["seat"] is None else str(row["seat"])
        if not spectator and seat is None:
            raise StoreForbidden("Guest cannot access this game")
        return {
            "game_id": str(row["game_id"]),
            "room_id": str(row["room_id"]),
            "status": str(row["status"]),
            "state_revision": int(row["state_revision"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "format_profile": str(row["format_profile"]),
            "seat": seat,
            "spectator": spectator,
            "role": "spectator" if spectator else "player",
            "owner": not spectator and row["owner_guest_id"] == guest_id,
        }

    def require_game_owner(self, game_id: str, guest_id: str) -> None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT r.owner_guest_id
                FROM games g JOIN rooms r ON r.room_id = g.room_id
                WHERE g.game_id = ?
                """,
                (game_id,),
            ).fetchone()
        if row is None:
            raise StoreNotFound("Game not found")
        if row["owner_guest_id"] != guest_id:
            raise StoreForbidden(
                "Only the room owner can control the game lifecycle"
            )

    def game_row(self, game_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM games WHERE game_id = ?", (game_id,)
            ).fetchone()
        if row is None:
            raise StoreNotFound("Game not found")
        return dict(row)

    def update_game_state(
        self, game_id: str, revision: int, status: str
    ) -> None:
        if status not in GAME_STATUSES:
            raise ValueError(f"Unknown server game status {status!r}")
        with self._connection(write=True) as connection:
            updated = connection.execute(
                """
                UPDATE games SET state_revision = ?, status = ?, updated_at = ?
                WHERE game_id = ?
                """,
                (revision, status, utc_now(), game_id),
            )
            if updated.rowcount != 1:
                raise StoreNotFound("Game not found")
            connection.execute(
                """
                UPDATE rooms SET status = ?, updated_at = ?
                WHERE room_id = (
                    SELECT room_id FROM games WHERE game_id = ?
                )
                """,
                (status, utc_now(), game_id),
            )

    def update_game_revision(self, game_id: str, revision: int) -> None:
        """Compatibility wrapper for callers that do not change lifecycle."""

        with self._connection() as connection:
            row = connection.execute(
                "SELECT status FROM games WHERE game_id = ?", (game_id,)
            ).fetchone()
        if row is None:
            raise StoreNotFound("Game not found")
        self.update_game_state(game_id, revision, str(row["status"]))

    def count_rows(self, table: str) -> int:
        if table not in {
            "guest_sessions",
            "rooms",
            "room_members",
            "room_seats",
            "decks",
            "games",
            "idempotency_records",
        }:
            raise ValueError("Unknown table")
        with self._connection() as connection:
            row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0])
