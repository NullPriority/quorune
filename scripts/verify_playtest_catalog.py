from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
from hashlib import file_digest
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorune.bulk import (
    SCRYFALL_BULK_DATA_URL,
    ScryfallBulkDataError,
    ScryfallBulkItem,
    fetch_bulk_manifest,
)


DATABASE_SOURCES = {"oracle_cards": "oracle", "rulings": "rulings"}


def _aware_timestamp(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    return result if result.utcoffset() is not None else None


def _database_metadata(path: Path) -> dict[str, str]:
    uri = path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        return dict(connection.execute("SELECT key, value FROM metadata"))


def _check_database_sources(
    metadata: dict[str, str], items: dict[str, ScryfallBulkItem], errors: list[str]
) -> dict[str, dict]:
    checks = {}
    for bulk_type, prefix in DATABASE_SOURCES.items():
        item = items[bulk_type]
        expected = {"updated_at": item.updated_at, "download_uri": item.download_uri}
        actual = {key: metadata.get(f"scryfall_{prefix}_{key}") for key in expected}
        expected_time = _aware_timestamp(expected["updated_at"])
        timestamp_matches = expected_time is not None and expected_time == _aware_timestamp(actual["updated_at"])
        url_matches = actual["download_uri"] == expected["download_uri"]
        checks[bulk_type] = {
            "expected": expected, "actual": actual,
            "timestamp_matches": timestamp_matches, "download_uri_matches": url_matches,
        }
        if expected_time is None:
            errors.append(f"Scryfall returned an invalid {item.name} timestamp. Retry the bulk metadata check.")
        elif not (timestamp_matches and url_matches):
            errors.append(
                f"The staged SQLite {item.name} provenance differs from Scryfall. Rebuild it from "
                f"the current {item.name} export at {item.download_uri} and rerun the check."
            )
    return checks


def _check_print_archive(
    path: Path, item: ScryfallBulkItem, expected_sha256: str | None, errors: list[str]
) -> dict:
    expected_name = Path(urlparse(item.download_uri).path).name
    result = {
        "path": str(path.resolve()), "name": path.name, "expected_name": expected_name,
        "updated_at": item.updated_at, "download_uri": item.download_uri,
        "expected_size_bytes": item.compressed_size, "expected_sha256": expected_sha256,
    }
    valid_size = item.compressed_size is not None and item.compressed_size > 0
    if _aware_timestamp(item.updated_at) is None or not expected_name or not valid_size:
        errors.append("Scryfall returned incomplete All Cards metadata. Retry the bulk metadata check.")
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            is_gzip = stream.read(3) == b"\x1f\x8b\x08"
            stream.seek(0)
            digest = file_digest(stream, "sha256").hexdigest()
    except OSError as exc:
        errors.append(f"Cannot read the staged All Cards archive {path}. Supply the current file. {exc}")
        return result

    name_matches = path.name == expected_name
    size_matches = valid_size and size == item.compressed_size
    sha256_matches = (digest == expected_sha256) if expected_sha256 is not None else None
    result.update(
        size_bytes=size, sha256=digest, name_matches=name_matches, size_matches=size_matches,
        gzip_header_matches=is_gzip, sha256_matches=sha256_matches,
    )
    if not (name_matches and size_matches and is_gzip):
        errors.append(
            f"The staged All Cards filename, byte count, or gzip signature differs from the current "
            f"export. Supply {expected_name} ({item.compressed_size} bytes) from {item.download_uri}."
        )
    if sha256_matches is False:
        errors.append(
            "The staged All Cards SHA-256 differs from the supplied digest. "
            "Check the trusted download receipt and replace the staged file if needed."
        )
    return result


def verify_playtest_catalog(
    database: Path, all_cards: Path, *, expected_all_cards_sha256: str | None = None
) -> dict:
    errors: list[str] = []
    report: dict = {
        "ok": False, "manifest_url": SCRYFALL_BULK_DATA_URL,
        "database": str(database.resolve()), "errors": errors,
    }
    try:
        items, _ = fetch_bulk_manifest()
    except (ScryfallBulkDataError, TypeError, ValueError) as exc:
        errors.append(f"Could not verify current Scryfall metadata. Retry before deployment. {exc}")
        return report
    report["queried_at_utc"] = datetime.now(timezone.utc).isoformat()
    missing = [kind for kind in (*DATABASE_SOURCES, "all_cards") if kind not in items]
    if missing:
        errors.append(
            f"Scryfall returned no usable metadata for {', '.join(missing)}. Retry before deployment."
        )
        return report

    try:
        metadata = _database_metadata(database)
    except (OSError, sqlite3.Error) as exc:
        errors.append(f"Cannot read staged SQLite catalog {database}. Supply a managed Scryfall database. {exc}")
    else:
        report["database_sources"] = _check_database_sources(metadata, items, errors)

    report["printing_archive"] = _check_print_archive(all_cards, items["all_cards"], expected_all_cards_sha256, errors)
    report["ok"] = not errors
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a staged playtest catalog against live Scryfall bulk metadata.")
    parser.add_argument("--db", type=Path, required=True, help="Staged Oracle and rulings SQLite database")
    parser.add_argument("--all-cards", type=Path, required=True, help="Staged gzip archive for all-language printings")
    parser.add_argument("--all-cards-sha256", help="Expected All Cards SHA-256 from a trusted download receipt")
    args = parser.parse_args()
    if args.all_cards_sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", args.all_cards_sha256):
        parser.error("--all-cards-sha256 requires exactly 64 hexadecimal digits")
    expected_sha256 = args.all_cards_sha256.lower() if args.all_cards_sha256 is not None else None
    report = verify_playtest_catalog(args.db, args.all_cards, expected_all_cards_sha256=expected_sha256)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
