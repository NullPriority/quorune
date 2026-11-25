from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timedelta, timezone
import mimetypes
from pathlib import Path
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from quorune.bulk import (
    SCRYFALL_BULK_DATA_URL,
    SCRYFALL_USER_AGENT,
    ScryfallBulkDataError,
    fetch_bulk_manifest,
    refresh_scryfall_database,
)
from quorune.carddb import CardDatabase
from quorune.card_printing import CardPrinting
from quorune.record import database_fingerprint


SCRYFALL_IMAGE_HOSTS = frozenset({"cards.scryfall.io"})
IMAGE_SIZES = frozenset({"small", "normal", "large", "png", "art_crop"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


class ManagedScryfallData:
    """Own the local bulk-data lifecycle without touching a running game DB.

    The first usable database is activated immediately. Later daily refreshes
    are fully downloaded and built beside it, then activated on the next server
    start. This preserves the database snapshot used by in-flight Game Records.
    """

    def __init__(
        self,
        database: Path,
        download_dir: Path,
        snapshot_dir: Path,
        game_root: Path,
        *,
        enabled: bool,
        interval_seconds: int = 24 * 60 * 60,
        manifest_url: str = SCRYFALL_BULK_DATA_URL,
    ) -> None:
        self.database = database
        self.download_dir = download_dir
        self.snapshot_dir = snapshot_dir
        self.game_root = game_root
        self.enabled = enabled
        self.interval_seconds = max(60, int(interval_seconds))
        self.manifest_url = manifest_url
        self.phase = "starting"
        self.detail = "Inspecting the local card database."
        self.ready = False
        self.updating = False
        self.restart_required = False
        self.last_checked_at: datetime | None = None
        self.next_check_at: datetime | None = None
        self.last_error: str | None = None
        self._activation_warning: str | None = None
        self.metadata: dict[str, str] = {}
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._refresh_lock = asyncio.Lock()
        self._on_ready: Callable[[Path], Awaitable[None]] | None = None
        self._ready_notified = False

    @property
    def pending_database(self) -> Path:
        return self.database.with_name(f"{self.database.stem}.next{self.database.suffix}")

    def _read_metadata(self, path: Path) -> dict[str, str]:
        with CardDatabase(path) as database:
            return database.metadata()

    def _activate_pending(self) -> None:
        pending = self.pending_database
        if not pending.exists():
            return
        self.database.parent.mkdir(parents=True, exist_ok=True)
        if self.database.exists():
            with CardDatabase(self.database) as current:
                current_hash = str(database_fingerprint(current)["metadata_hash"])
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)
            snapshot = self.snapshot_dir / f"{current_hash}.sqlite3"
            if snapshot.exists():
                self.database.unlink()
            else:
                self.database.replace(snapshot)
        pending.replace(self.database)
        self._prune_unreferenced_snapshots()

    def _referenced_database_hashes(self) -> set[str]:
        hashes: set[str] = set()
        if not self.game_root.is_dir():
            return hashes
        for manifest_path in self.game_root.glob("*/manifest.json"):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                value = payload.get("scryfall", {}).get("metadata_hash")
                if isinstance(value, str) and value:
                    hashes.add(value)
            except (OSError, json.JSONDecodeError, AttributeError):
                continue
        return hashes

    def _prune_unreferenced_snapshots(self) -> None:
        if not self.snapshot_dir.is_dir():
            return
        retained = self._referenced_database_hashes()
        for candidate in self.snapshot_dir.glob("*.sqlite3"):
            if candidate.stem not in retained:
                candidate.unlink()

    async def start(self, on_ready: Callable[[Path], Awaitable[None]]) -> None:
        self._on_ready = on_ready
        if self.pending_database.exists():
            try:
                await asyncio.to_thread(self._activate_pending)
            except PermissionError:
                if not self.database.exists():
                    raise
                self._activation_warning = (
                    "A newer card database is ready, but the active database is locked by "
                    "another process. The existing card database remains available; close "
                    "other Quorune servers and restart to activate the update."
                )
                self.restart_required = True
        if self.database.exists():
            self.metadata = await asyncio.to_thread(self._read_metadata, self.database)
            if not self.enabled or self._activation_warning is not None:
                self.ready = True
                if self._activation_warning is not None:
                    self.phase = "update_ready"
                    self.detail = self._activation_warning
                else:
                    self.phase = "ready"
                    self.detail = "Local card database is ready."
                await self._notify_ready()
        elif not self.enabled:
            self.phase = "error"
            self.detail = "The configured card database does not exist and automatic setup is disabled."
            self.last_error = self.detail
            return
        if not self.enabled:
            return
        self._task = asyncio.create_task(self._run(), name="scryfall-data-refresh")

    async def _notify_ready(self) -> None:
        if self._ready_notified or self._on_ready is None:
            return
        await self._on_ready(self.database)
        self._ready_notified = True

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    def request_refresh(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        while True:
            await self.refresh()
            self.next_check_at = _utc_now() + timedelta(seconds=self.interval_seconds)
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass

    async def refresh(self) -> None:
        if not self.enabled:
            return
        async with self._refresh_lock:
            had_database = self.database.exists()
            self.updating = True
            self.phase = "checking" if had_database else "downloading"
            self.detail = (
                "Checking Scryfall for updated Oracle and rulings exports."
                if had_database
                else "Downloading Scryfall Oracle cards and rulings for first-time setup."
            )
            self.last_error = None
            try:
                items, _ = await asyncio.to_thread(
                    fetch_bulk_manifest,
                    url=self.manifest_url,
                    timeout=60,
                )
                missing = [kind for kind in ("oracle_cards", "rulings") if kind not in items]
                if missing:
                    raise ScryfallBulkDataError(
                        "Scryfall manifest omitted required bulk item(s): " + ", ".join(missing)
                    )
                existing = self.metadata if had_database else {}
                current = (
                    existing.get("scryfall_oracle_updated_at") == items["oracle_cards"].updated_at
                    and existing.get("scryfall_rulings_updated_at") == items["rulings"].updated_at
                )
                if current:
                    self.phase = "ready"
                    self.detail = "Card database is current."
                    self.ready = True
                    await self._notify_ready()
                    return
                if had_database and self.pending_database.exists():
                    pending_metadata = await asyncio.to_thread(
                        self._read_metadata, self.pending_database
                    )
                    pending_current = (
                        pending_metadata.get("scryfall_oracle_updated_at")
                        == items["oracle_cards"].updated_at
                        and pending_metadata.get("scryfall_rulings_updated_at")
                        == items["rulings"].updated_at
                    )
                    if pending_current:
                        self.restart_required = True
                        self.phase = "update_ready"
                        self.detail = "A newer card database is ready and will activate on the next server restart."
                        return

                self.phase = "building"
                self.detail = "Downloading exports and building an indexed SQLite database."
                destination = self.pending_database if had_database else self.database
                await asyncio.to_thread(
                    refresh_scryfall_database,
                    destination,
                    download_dir=self.download_dir,
                    manifest_url=self.manifest_url,
                    timeout=120,
                )
                built_metadata = await asyncio.to_thread(self._read_metadata, destination)
                if had_database:
                    if not self._ready_notified:
                        await asyncio.to_thread(self._activate_pending)
                        self.metadata = await asyncio.to_thread(
                            self._read_metadata, self.database
                        )
                        self.ready = True
                        self.restart_required = False
                        self.phase = "ready"
                        self.detail = "Updated card database is ready."
                        await self._notify_ready()
                    else:
                        self.restart_required = True
                        self.phase = "update_ready"
                        self.detail = "A newer card database is ready and will activate on the next server restart."
                else:
                    self.metadata = built_metadata
                    self.ready = True
                    self.phase = "ready"
                    self.detail = "Card database is ready."
                    await self._notify_ready()
            except Exception as exc:
                self.last_error = str(exc)
                if had_database:
                    self.ready = True
                    self.phase = "ready_with_warning"
                    self.detail = "The existing card database is usable, but the update check failed."
                    await self._notify_ready()
                else:
                    self.ready = False
                    self.phase = "error"
                    self.detail = "Card-data setup failed. Retry when network access is available."
            finally:
                self.updating = False
                self.last_checked_at = _utc_now()

    def status(self) -> dict[str, Any]:
        errors = tuple(
            dict.fromkeys(
                value
                for value in (self._activation_warning, self.last_error)
                if value
            )
        )
        return {
            "ready": self.ready,
            "phase": self.phase,
            "detail": self.detail,
            "updating": self.updating,
            "automatic_updates": self.enabled,
            "update_interval_hours": self.interval_seconds / 3600,
            "restart_required": self.restart_required,
            "last_checked_at": _iso(self.last_checked_at),
            "next_check_at": _iso(self.next_check_at),
            "last_error": " ".join(errors) or None,
            "database": {
                "cards": int(self.metadata.get("card_count", "0")),
                "rulings": int(self.metadata.get("ruling_count", "0")),
                "image_references": int(self.metadata.get("image_reference_count", "0")),
                "oracle_updated_at": self.metadata.get("scryfall_oracle_updated_at"),
                "rulings_updated_at": self.metadata.get("scryfall_rulings_updated_at"),
                "retained_game_snapshots": len(list(self.snapshot_dir.glob("*.sqlite3"))) if self.snapshot_dir.is_dir() else 0,
            },
        }


class CardImageCache:
    """Download Scryfall CDN images once and serve the local cached copy."""

    def __init__(self, root: Path, databases: Callable[[], Iterable[CardDatabase]]) -> None:
        self.root = root
        self._databases = databases
        self._locks: dict[str, asyncio.Lock] = {}
        self._printing_lock = asyncio.Lock()
        self._next_printing_lookup = 0.0
        self._downloaded = sum(1 for path in root.rglob("*") if path.is_file()) if root.exists() else 0

    @property
    def downloaded(self) -> int:
        return self._downloaded

    @staticmethod
    def _printing_path(printing: CardPrinting) -> str:
        if printing.scryfall_id:
            return printing.scryfall_id
        if printing.set_code and printing.collector_number:
            language = printing.language or "en"
            if (not re.fullmatch(r"[a-zA-Z0-9]+", printing.set_code)
                    or not re.fullmatch(r"[a-zA-Z0-9]+", language)
                    or not re.fullmatch(r"[\w][\w.+*★-]*", printing.collector_number)):
                raise KeyError("The imported set or language is unavailable")
            return "/".join(urllib.parse.quote(value, safe="") for value in (
                printing.set_code, printing.collector_number, language,
            ))
        raise KeyError("The imported printing has no Scryfall identity")

    @staticmethod
    def _printing_image(path: str, oracle_prefix: str, face: int, size: str) -> str:
        request = urllib.request.Request(
            f"https://api.scryfall.com/cards/{path}",
            headers={"Accept": "application/json", "User-Agent": SCRYFALL_USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                card = json.loads(response.read(1024 * 1024))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise KeyError("The imported printing image is unavailable") from exc
        oracle_id = card.get("oracle_id")
        if not isinstance(oracle_id, str) or not oracle_id.casefold().startswith(oracle_prefix.casefold()):
            raise KeyError("The imported printing does not match this Oracle card")
        faces = card.get("card_faces") or []
        image_uris = card.get("image_uris") if face == 0 else None
        if not image_uris and face < len(faces):
            image_uris = faces[face].get("image_uris")
        source = (image_uris or {}).get(size)
        if not isinstance(source, str):
            raise KeyError("No image is available for this printing and face")
        parsed = urllib.parse.urlparse(source)
        if parsed.scheme.casefold() != "https" or (parsed.hostname or "").casefold() not in SCRYFALL_IMAGE_HOSTS:
            raise ValueError("Image URL is not on the approved Scryfall CDN")
        return source

    def _source(self, oracle_prefix: str, face: int, size: str) -> tuple[str, str]:
        if size not in IMAGE_SIZES:
            raise ValueError("Unsupported image size")
        databases = tuple(self._databases())
        if not databases:
            raise KeyError("Card database is not ready")
        row = None
        for database in databases:
            rows = database.image_uris(oracle_prefix)
            row = next((candidate for candidate in rows if candidate["face_index"] == face and candidate.get(size)), None)
            if row is not None:
                break
        if row is None:
            raise KeyError("No image is available for this card face")
        source = str(row[size])
        parsed = urllib.parse.urlparse(source)
        if parsed.scheme.casefold() != "https" or (parsed.hostname or "").casefold() not in SCRYFALL_IMAGE_HOSTS:
            raise ValueError("Image URL is not on the approved Scryfall CDN")
        return str(row["oracle_id"]), source

    @staticmethod
    def _download(source: str, destination: Path) -> str:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".part")
        if temporary.exists():
            temporary.unlink()
        request = urllib.request.Request(
            source,
            headers={"Accept": "image/*", "User-Agent": SCRYFALL_USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response, temporary.open("wb") as output:
                content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0]
                if not content_type.startswith("image/"):
                    raise ValueError("Scryfall image response was not an image")
                total = 0
                while chunk := response.read(256 * 1024):
                    total += len(chunk)
                    if total > 20 * 1024 * 1024:
                        raise ValueError("Scryfall image exceeded the 20 MiB safety limit")
                    output.write(chunk)
            temporary.replace(destination)
            return content_type
        except Exception:
            if temporary.exists():
                temporary.unlink()
            raise

    async def get(
        self, oracle_prefix: str, *, face: int = 0, size: str = "normal",
        printing: CardPrinting | None = None,
    ) -> tuple[Path, str]:
        if printing is None:
            oracle_id, source = self._source(oracle_prefix, face, size)
            printing_path = None
            suffix = Path(urllib.parse.urlparse(source).path).suffix.casefold()
        else:
            printing_path = self._printing_path(printing)
            fingerprint = hashlib.sha256(f"{oracle_prefix}:{printing_path}".encode()).hexdigest()[:28]
            oracle_id, source, suffix = f"printing-{fingerprint}", "", ""
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            suffix = ".png" if size == "png" else ".jpg"
        destination = self.root / oracle_id / f"{face}-{size}{suffix}"
        if destination.is_file() and destination.stat().st_size > 0:
            return destination, mimetypes.guess_type(destination.name)[0] or "image/jpeg"
        key = str(destination)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if destination.is_file() and destination.stat().st_size > 0:
                return destination, mimetypes.guess_type(destination.name)[0] or "image/jpeg"
            if printing_path:
                async with self._printing_lock:
                    await asyncio.sleep(max(0, self._next_printing_lookup - time.monotonic()))
                    self._next_printing_lookup = time.monotonic() + 0.12
                    source = await asyncio.to_thread(self._printing_image, printing_path, oracle_prefix, face, size)
            content_type = await asyncio.to_thread(self._download, source, destination)
            self._downloaded += 1
            return destination, content_type

    async def prefetch(self, oracle_ids: Iterable[str], *, concurrency: int = 4) -> None:
        semaphore = asyncio.Semaphore(max(1, min(concurrency, 8)))

        async def one(oracle_id: str) -> None:
            async with semaphore:
                try:
                    await self.get(oracle_id, size="normal")
                except (KeyError, ValueError, OSError, urllib.error.URLError):
                    return

        await asyncio.gather(*(one(value) for value in dict.fromkeys(oracle_ids)))
