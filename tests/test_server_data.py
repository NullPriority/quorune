from __future__ import annotations

import asyncio
from contextlib import closing
from io import BytesIO
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from quorune.bulk import ScryfallBulkItem
from quorune.carddb import CardDatabase, build_card_database
from quorune.record import database_fingerprint
from server.app import ServerSettings, create_app
from server.data import CardImageCache, ManagedScryfallData


class _ImageResponse(BytesIO):
    def __init__(self, payload: bytes):
        super().__init__(payload)
        self.headers = {"Content-Type": "image/jpeg", "Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _build_database(root: Path, *, image_host: str | None = "cards.scryfall.io") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    oracle = root / "oracle.jsonl"
    rulings = root / "rulings.jsonl"
    oracle.write_text(
        json.dumps(
            {
                "oracle_id": "12345678-1234-1234-1234-1234567890ab",
                "name": "Setup Test Card",
                "mana_cost": "{1}",
                "cmc": 1,
                "type_line": "Artifact",
                "oracle_text": "{T}: Add {C}.",
                "legalities": {"commander": "legal"},
                "image_uris": {
                    "small": f"https://{image_host}/small/front/test.jpg",
                    "normal": f"https://{image_host}/normal/front/test.jpg",
                } if image_host is not None else {},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    rulings.write_text("", encoding="utf-8")
    database = root / "cards.sqlite3"
    build_card_database(oracle, rulings, database)
    with closing(sqlite3.connect(database)) as connection:
        connection.executemany(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            [
                ("scryfall_oracle_updated_at", "old-oracle"),
                ("scryfall_rulings_updated_at", "old-rulings"),
            ],
        )
        connection.commit()
    return database


class CardImageDatabaseTests(unittest.TestCase):
    def test_bulk_database_indexes_image_references_by_projected_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            database_path = _build_database(Path(temporary))
            with CardDatabase(database_path) as database:
                rows = database.image_uris("12345678")
                self.assertEqual(1, len(rows))
                self.assertEqual(
                    "https://cards.scryfall.io/normal/front/test.jpg",
                    rows[0]["normal"],
                )
                self.assertEqual("1", database.metadata()["image_reference_count"])

    def test_compact_rules_database_can_serve_art_from_a_separate_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compact = _build_database(root / "compact", image_host=None)
            art = _build_database(root / "art")
            app = create_app(ServerSettings(
                card_db=compact,
                database=root / "server.sqlite3",
                game_root=root / "games",
                image_cache=root / "images",
                card_image_db=art,
            ))
            with TestClient(app) as client, patch(
                "server.data.urllib.request.urlopen", return_value=_ImageResponse(b"jpeg-bytes")
            ) as download:
                response = client.get("/api/v1/cards/12345678/image?size=normal")
                self.assertEqual(200, response.status_code)
                self.assertEqual(b"jpeg-bytes", response.content)
                self.assertEqual(compact, app.state.runtime.card_db.path)
                self.assertEqual("https://cards.scryfall.io/normal/front/test.jpg", download.call_args.args[0].full_url)


class CardImageCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_is_downloaded_once_and_then_served_from_local_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = CardDatabase(_build_database(root))
            cache = CardImageCache(root / "images", lambda: (database,))
            calls = []

            def urlopen(request, timeout):
                calls.append((request.full_url, timeout))
                return _ImageResponse(b"jpeg-bytes")

            try:
                with patch("server.data.urllib.request.urlopen", side_effect=urlopen):
                    first, media_type = await cache.get("12345678", size="normal")
                    second, _ = await cache.get("12345678", size="normal")
                self.assertEqual(first, second)
                self.assertEqual(b"jpeg-bytes", first.read_bytes())
                self.assertEqual("image/jpeg", media_type)
                self.assertEqual(1, len(calls))
                self.assertEqual(1, cache.downloaded)
            finally:
                database.close()

    async def test_non_scryfall_image_host_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = CardDatabase(_build_database(root, image_host="attacker.invalid"))
            cache = CardImageCache(root / "images", lambda: (database,))
            try:
                with self.assertRaises(ValueError):
                    await cache.get("12345678", size="normal")
            finally:
                database.close()


class ManagedScryfallDataTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _items() -> dict[str, ScryfallBulkItem]:
        return {
            "oracle_cards": ScryfallBulkItem(
                "oracle_cards",
                "Oracle Cards",
                "new-oracle",
                "https://data.scryfall.io/oracle.jsonl.gz",
            ),
            "rulings": ScryfallBulkItem(
                "rulings",
                "Rulings",
                "new-rulings",
                "https://data.scryfall.io/rulings.jsonl.gz",
            ),
        }

    async def test_daily_update_is_built_beside_active_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = _build_database(root)
            manager = ManagedScryfallData(
                database,
                root / "bulk",
                root / "snapshots",
                root / "games",
                enabled=True,
            )
            manager.metadata = manager._read_metadata(database)
            manager.ready = True
            manager._ready_notified = True

            def refresh(destination, **_kwargs):
                shutil.copy2(database, destination)
                with closing(sqlite3.connect(destination)) as connection:
                    connection.executemany(
                        "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                        [
                            ("scryfall_oracle_updated_at", "new-oracle"),
                            ("scryfall_rulings_updated_at", "new-rulings"),
                        ],
                    )
                    connection.commit()
                return {}

            with (
                patch("server.data.fetch_bulk_manifest", return_value=(self._items(), {})),
                patch("server.data.refresh_scryfall_database", side_effect=refresh),
            ):
                await manager.refresh()
            self.assertTrue(manager.ready)
            self.assertTrue(manager.restart_required)
            self.assertEqual("update_ready", manager.phase)
            self.assertTrue(manager.pending_database.is_file())
            self.assertEqual("old-oracle", manager.metadata["scryfall_oracle_updated_at"])

    async def test_startup_activates_a_stale_database_before_runtime_is_ready(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = _build_database(root)
            manager = ManagedScryfallData(
                database,
                root / "bulk",
                root / "snapshots",
                root / "games",
                enabled=True,
            )
            runtime_ready = asyncio.Event()
            ready_metadata = []

            async def on_ready(path: Path) -> None:
                ready_metadata.append(manager._read_metadata(path))
                runtime_ready.set()

            def refresh(destination, **_kwargs):
                shutil.copy2(database, destination)
                with closing(sqlite3.connect(destination)) as connection:
                    connection.executemany(
                        "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                        [
                            ("scryfall_oracle_updated_at", "new-oracle"),
                            ("scryfall_rulings_updated_at", "new-rulings"),
                        ],
                    )
                    connection.commit()
                return {}

            try:
                with (
                    patch("server.data.fetch_bulk_manifest", return_value=(self._items(), {})),
                    patch("server.data.refresh_scryfall_database", side_effect=refresh),
                ):
                    await manager.start(on_ready)
                    self.assertFalse(manager.ready)
                    await asyncio.wait_for(runtime_ready.wait(), timeout=2)
                self.assertTrue(manager.ready)
                self.assertFalse(manager.restart_required)
                self.assertEqual("ready", manager.phase)
                self.assertEqual("new-oracle", ready_metadata[0]["scryfall_oracle_updated_at"])
                self.assertFalse(manager.pending_database.exists())
            finally:
                await manager.close()

    async def test_startup_uses_current_database_when_pending_activation_is_locked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = _build_database(root)
            manager = ManagedScryfallData(
                database,
                root / "bulk",
                root / "snapshots",
                root / "games",
                enabled=False,
            )
            shutil.copy2(database, manager.pending_database)
            ready_paths = []

            async def on_ready(path: Path) -> None:
                ready_paths.append(path)

            with patch.object(
                manager,
                "_activate_pending",
                side_effect=PermissionError(32, "database is in use"),
            ):
                await manager.start(on_ready)

            status = manager.status()
            self.assertTrue(manager.ready)
            self.assertTrue(manager.restart_required)
            self.assertEqual("update_ready", manager.phase)
            self.assertEqual([database], ready_paths)
            self.assertTrue(manager.pending_database.exists())
            self.assertIn("locked by another process", status["last_error"])

    async def test_missing_database_becomes_ready_after_managed_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _build_database(root / "source")
            destination = root / "managed" / "cards.sqlite3"
            manager = ManagedScryfallData(
                destination,
                root / "bulk",
                root / "snapshots",
                root / "games",
                enabled=True,
            )
            ready_paths = []

            async def on_ready(path: Path) -> None:
                ready_paths.append(path)

            manager._on_ready = on_ready

            def refresh(path, **_kwargs):
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, path)
                with closing(sqlite3.connect(path)) as connection:
                    connection.executemany(
                        "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                        [
                            ("scryfall_oracle_updated_at", "new-oracle"),
                            ("scryfall_rulings_updated_at", "new-rulings"),
                        ],
                    )
                    connection.commit()
                return {}

            with (
                patch("server.data.fetch_bulk_manifest", return_value=(self._items(), {})),
                patch("server.data.refresh_scryfall_database", side_effect=refresh),
            ):
                await manager.refresh()
            self.assertTrue(manager.ready)
            self.assertEqual("ready", manager.phase)
            self.assertEqual([destination], ready_paths)

    async def test_activation_retains_only_database_snapshot_referenced_by_game(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = _build_database(root / "active")
            games = root / "games"
            snapshots = root / "snapshots"
            manager = ManagedScryfallData(
                database,
                root / "bulk",
                snapshots,
                games,
                enabled=True,
            )
            with CardDatabase(database) as active:
                active_hash = str(database_fingerprint(active)["metadata_hash"])
            pending_source = _build_database(root / "new")
            shutil.copy2(pending_source, manager.pending_database)
            game = games / "game-one"
            game.mkdir(parents=True)
            (game / "manifest.json").write_text(
                json.dumps({"scryfall": {"metadata_hash": active_hash}}),
                encoding="utf-8",
            )

            manager._activate_pending()

            retained = snapshots / f"{active_hash}.sqlite3"
            self.assertTrue(database.is_file())
            self.assertTrue(retained.is_file())
            self.assertFalse(manager.pending_database.exists())
            shutil.rmtree(game)
            manager._prune_unreferenced_snapshots()
            self.assertFalse(retained.exists())


if __name__ == "__main__":
    unittest.main()
