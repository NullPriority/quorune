from __future__ import annotations

from contextlib import closing, redirect_stdout
import gzip
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from quorune.bulk import (
    ScryfallBulkDataError,
    ScryfallBulkItem,
    _download_bulk_item,
    _prune_managed_bulk_cache,
    build_pinned_scryfall_database,
    fetch_bulk_manifest,
    parse_bulk_manifest,
)
from quorune.carddb import CardDatabase
from scripts import verify_playtest_catalog


class _Response(io.BytesIO):
    def __init__(self, value: bytes, headers=None):
        super().__init__(value)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class ScryfallBulkDataTests(unittest.TestCase):
    def test_pinned_snapshot_build_verifies_archives_counts_and_metadata(self):
        card = {
            "oracle_id": "00000000-0000-4000-8000-000000000001",
            "name": "Pinned Cloud Fixture",
            "mana_cost": "{1}",
            "cmc": 1,
            "type_line": "Artifact",
            "oracle_text": "",
            "colors": [],
            "color_identity": [],
            "keywords": [],
            "produced_mana": [],
            "layout": "normal",
            "released_at": "2026-01-01",
            "legalities": {"commander": "legal"},
        }
        archives = {
            "https://data.scryfall.io/oracle-pinned.jsonl.gz": gzip.compress(
                (json.dumps(card) + "\n").encode("utf-8")
            ),
            "https://data.scryfall.io/rulings-pinned.jsonl.gz": gzip.compress(
                b""
            ),
        }
        snapshot = {
            "available": True,
            "database_schema_version": "2",
            "oracle_bulk": {
                "updated_at": "2026-08-06T09:02:54Z",
                "download_uri": next(iter(archives)),
                "sha256": hashlib.sha256(
                    archives[next(iter(archives))]
                ).hexdigest(),
                "oracle_id_count": 1,
            },
            "rulings_bulk": {
                "updated_at": "2026-08-06T09:00:37Z",
                "download_uri": list(archives)[1],
                "sha256": hashlib.sha256(
                    archives[list(archives)[1]]
                ).hexdigest(),
                "ruling_count": 0,
            },
        }

        def fake_urlopen(request, timeout):
            self.assertEqual(7, timeout)
            payload = archives[request.full_url]
            return _Response(payload, {"Content-Length": str(len(payload))})

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "pinned.sqlite3"
            result = build_pinned_scryfall_database(
                snapshot,
                output,
                download_dir=root / "bulk",
                timeout=7,
                urlopen=fake_urlopen,
            )
            with CardDatabase(output) as database:
                metadata = database.metadata()

        self.assertEqual(1, result["cards"])
        self.assertEqual("rules/manifest.json", result["snapshot_source"])
        self.assertEqual(
            snapshot["oracle_bulk"]["sha256"],
            metadata["oracle_source_sha256"],
        )
        self.assertEqual(
            snapshot["oracle_bulk"]["download_uri"],
            metadata["scryfall_oracle_download_uri"],
        )

    def test_pinned_snapshot_rejects_untrusted_download_host(self):
        with self.assertRaises(ScryfallBulkDataError):
            build_pinned_scryfall_database(
                {
                    "available": True,
                    "database_schema_version": "2",
                    "oracle_bulk": {
                        "updated_at": "now",
                        "download_uri": "https://attacker.invalid/oracle.gz",
                        "sha256": "a" * 64,
                        "oracle_id_count": 1,
                    },
                    "rulings_bulk": {
                        "updated_at": "now",
                        "download_uri": "https://data.scryfall.io/rulings.gz",
                        "sha256": "b" * 64,
                        "ruling_count": 0,
                    },
                },
                "ignored.sqlite3",
                download_dir="ignored-bulk",
            )

    def test_manifest_prefers_streamable_jsonl_and_ignores_untrusted_hosts(self):
        items = parse_bulk_manifest(
            {
                "object": "list",
                "data": [
                    {
                        "type": "oracle_cards",
                        "name": "Oracle Cards",
                        "updated_at": "2026-07-28T20:11:20Z",
                        "download_uri": "https://data.scryfall.io/oracle.json",
                        "jsonl_download_uri": "https://data.scryfall.io/oracle.jsonl.gz",
                        "compressed_size": 123,
                    },
                    {
                        "type": "rulings",
                        "jsonl_download_uri": "https://attacker.invalid/rulings.jsonl.gz",
                    },
                ],
            }
        )
        self.assertEqual("https://data.scryfall.io/oracle.jsonl.gz", items["oracle_cards"].download_uri)
        self.assertEqual(123, items["oracle_cards"].compressed_size)
        self.assertNotIn("rulings", items)

    def test_fetch_manifest_uses_runtime_response(self):
        payload = {
            "object": "list",
            "data": [
                {
                    "type": "rulings",
                    "name": "Rulings",
                    "updated_at": "now",
                    "jsonl_download_uri": "https://data.scryfall.io/rulings-current.jsonl.gz",
                }
            ],
        }
        calls = []

        def fake_urlopen(request, timeout):
            calls.append((request.full_url, timeout, request.get_header("User-agent")))
            return _Response(json.dumps(payload).encode("utf-8"))

        items, returned = fetch_bulk_manifest(timeout=7, urlopen=fake_urlopen)
        self.assertEqual(payload, returned)
        self.assertIn("rulings", items)
        self.assertEqual("https://api.scryfall.com/bulk-data", calls[0][0])
        self.assertEqual(7, calls[0][1])
        self.assertIn("quorune", calls[0][2])

    def test_invalid_manifest_fails_closed(self):
        with self.assertRaises(ScryfallBulkDataError):
            parse_bulk_manifest({"object": "card", "data": []})

    def test_download_validates_http_length_not_inconsistent_manifest_size(self):
        item = ScryfallBulkItem(
            type="rulings",
            name="Rulings",
            updated_at="now",
            download_uri="https://data.scryfall.io/rulings.jsonl.gz",
            compressed_size=999,
        )

        def fake_urlopen(_request, timeout):
            self.assertEqual(3, timeout)
            return _Response(b"abc", {"Content-Length": "3"})

        with tempfile.TemporaryDirectory() as directory:
            path = _download_bulk_item(
                item,
                Path(directory),
                timeout=3,
                force=False,
                urlopen=fake_urlopen,
            )
            self.assertEqual(b"abc", path.read_bytes())

    def test_successful_refresh_retains_only_current_managed_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current_oracle = root / "oracle-cards-current.jsonl.gz"
            current_rulings = root / "rulings-current.jsonl.gz"
            old_oracle = root / "oracle-cards-old.jsonl.gz"
            old_rulings = root / "rulings-old.jsonl.gz"
            stale_partial = root / "oracle-cards-failed.jsonl.gz.part"
            user_file = root / "custom-card-data.jsonl.gz"
            for path in (
                current_oracle,
                current_rulings,
                old_oracle,
                old_rulings,
                stale_partial,
                user_file,
            ):
                path.write_bytes(b"fixture")

            removed = _prune_managed_bulk_cache(
                root, {current_oracle, current_rulings}
            )

            self.assertEqual(
                {old_oracle, old_rulings, stale_partial}, set(removed)
            )
            self.assertTrue(current_oracle.exists())
            self.assertTrue(current_rulings.exists())
            self.assertTrue(user_file.exists())


class PlaytestCatalogPreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.database = root / "staged cards.sqlite3"
        self.archive = root / "all-cards-latest.jsonl.gz"
        self.archive.write_bytes(gzip.compress(b""))
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.oracle = ScryfallBulkItem(
            "oracle_cards", "Oracle Cards", "2026-11-26T10:02:03.092+00:00",
            "https://data.scryfall.io/oracle-cards/oracle-current.jsonl.gz",
        )
        self.rulings = ScryfallBulkItem(
            "rulings", "Rulings", "2026-11-26T10:00:32.195Z",
            "https://data.scryfall.io/rulings/rulings-current.jsonl.gz",
        )
        self.printings = ScryfallBulkItem(
            "all_cards", "All Cards", "2026-11-26T10:23:31.002+00:00",
            f"https://data.scryfall.io/all-cards/{self.archive.name}",
            self.archive.stat().st_size,
        )
        self.items = {item.type: item for item in (self.oracle, self.rulings, self.printings)}
        self.metadata = {
            "scryfall_oracle_updated_at": "2026-11-26T04:02:03.092-06:00",
            "scryfall_oracle_download_uri": self.oracle.download_uri,
            "scryfall_rulings_updated_at": "2026-11-26T10:00:32.195+00:00",
            "scryfall_rulings_download_uri": self.rulings.download_uri,
        }

    def build_staged_database(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.executemany("INSERT INTO metadata VALUES (?, ?)", self.metadata.items())

    def run_command(self, *options):
        output = io.StringIO()
        arguments = ["verify_playtest_catalog.py", "--db", str(self.database),
                     "--all-cards", str(self.archive), *options]
        with patch.object(verify_playtest_catalog, "fetch_bulk_manifest", return_value=(self.items, {})) as fetch:
            with patch.object(sys, "argv", arguments), redirect_stdout(output):
                status = verify_playtest_catalog.main()
            fetch.assert_called_once_with()
        return status, json.loads(output.getvalue())

    def test_current_inputs_compares_equivalent_timestamps_and_verifies_digest(self):
        self.build_staged_database()
        original_bytes = self.database.read_bytes()
        status, result = self.run_command("--all-cards-sha256", self.digest.upper())
        self.assertEqual(0, status)
        self.assertTrue(result["ok"], result)
        self.assertEqual([], result["errors"])
        for row in result["database_sources"].values():
            self.assertTrue(row["timestamp_matches"])
            self.assertTrue(row["download_uri_matches"])
        archive = result["printing_archive"]
        self.assertEqual(self.archive.name, archive["expected_name"])
        self.assertEqual(self.archive.stat().st_size, archive["expected_size_bytes"])
        self.assertEqual(self.digest, archive["sha256"])
        self.assertEqual(self.digest, archive["expected_sha256"])
        self.assertTrue(all(archive[key] for key in (
            "name_matches", "size_matches", "gzip_header_matches", "sha256_matches",
        )))
        self.assertEqual(original_bytes, self.database.read_bytes())

    def test_stale_database_and_printings_report_how_to_replace_each_input(self):
        self.metadata["scryfall_oracle_download_uri"] = self.oracle.download_uri + "?old"
        self.metadata["scryfall_rulings_updated_at"] = "2026-11-26T10:00:32.194+00:00"
        self.build_staged_database()
        self.archive = self.archive.with_name("all-cards-previous.jsonl.gz")
        self.archive.write_bytes(gzip.compress(b"stale content"))
        status, result = self.run_command()
        self.assertEqual(1, status)
        self.assertFalse(result["ok"])
        oracle = result["database_sources"]["oracle_cards"]
        rulings = result["database_sources"]["rulings"]
        self.assertEqual((True, False), (oracle["timestamp_matches"], oracle["download_uri_matches"]))
        self.assertEqual((False, True), (rulings["timestamp_matches"], rulings["download_uri_matches"]))
        self.assertTrue(all(token in " ".join(result["errors"]) for token in (
            self.oracle.download_uri, self.rulings.download_uri, self.printings.download_uri,
        )))

    def test_invalid_local_inputs_and_missing_metadata_fail_closed(self):
        self.metadata["scryfall_rulings_updated_at"] = "2026-11-26T10:00:32.195"
        self.build_staged_database()
        status, result = self.run_command("--all-cards-sha256", "0" * 64)
        self.assertEqual(1, status)
        self.assertFalse(result["database_sources"]["rulings"]["timestamp_matches"])
        self.assertTrue(any("differs from the supplied digest" in message for message in result["errors"]))

        self.database = self.database.with_name("does-not-exist.sqlite3")
        status, result = self.run_command()
        self.assertEqual(1, status)
        self.assertTrue(any("Supply a managed Scryfall database" in message for message in result["errors"]))
        self.assertFalse(self.database.exists())

        del self.items["all_cards"]
        status, result = self.run_command()
        self.assertEqual(1, status)
        self.assertIn("all_cards", result["errors"][0])


if __name__ == "__main__":
    unittest.main()
