from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
from typing import Mapping, Sequence

from scripts.source_tree_fingerprint import canonical_tracked_blob_oids


DATABASE_IDENTITY_ALGORITHM = "pinned-card-database-provenance-sha256-v2"
_DATABASE_BUILDER_INPUTS = (
    "quorune/bulk.py",
    "quorune/carddb.py",
    "quorune/util.py",
    "rules/manifest.json",
    "scripts/bootstrap_data.py",
    "scripts/generated_cache_identity.py",
    "scripts/source_tree_fingerprint.py",
)


class GeneratedOwnerCacheError(RuntimeError):
    """An owner input closure, receipt, or reusable artifact is invalid."""


def canonical_json_fingerprint(algorithm: str, value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256()
    digest.update((algorithm + "\0").encode("ascii"))
    digest.update(encoded)
    return digest.hexdigest()


def path_entries(root: Path, paths: Sequence[str]) -> tuple[tuple[str, str], ...]:
    try:
        identities = canonical_tracked_blob_oids(root, list(paths))
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise GeneratedOwnerCacheError(
            "unable to compute Git-clean generated owner inputs"
        ) from exc
    return tuple(zip(paths, identities, strict=True))


def database_builder_input_fingerprint(*, root: Path) -> str:
    for relative in _DATABASE_BUILDER_INPUTS:
        if not (root / relative).is_file():
            raise GeneratedOwnerCacheError(f"pinned database builder input is missing: {relative}")
    return canonical_json_fingerprint(
        DATABASE_IDENTITY_ALGORITHM, dict(path_entries(root, _DATABASE_BUILDER_INPUTS))
    )


def pinned_database_identity(database: Path, *, root: Path) -> str:
    """Validate the pinned snapshot before fingerprinting its SQLite shape."""
    from quorune.bulk import ScryfallBulkDataError, _pinned_bulk_snapshot

    try:
        manifest = json.loads((root / "rules" / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GeneratedOwnerCacheError("pinned rules manifest is unavailable") from exc
    snapshot = manifest.get("card_data_snapshot") if isinstance(manifest, Mapping) else None
    if not isinstance(snapshot, Mapping):
        raise GeneratedOwnerCacheError("pinned rules manifest has no card-data snapshot")
    try:
        schema_version, _, source_hashes, expected_counts = _pinned_bulk_snapshot(snapshot)
    except ScryfallBulkDataError as exc:
        raise GeneratedOwnerCacheError("pinned rules manifest has invalid database provenance") from exc

    expected_metadata = {
        "schema_version": schema_version,
        "oracle_source_sha256": source_hashes["oracle_cards"],
        "rulings_source_sha256": source_hashes["rulings"],
        "card_count": str(expected_counts["oracle_cards"]),
        "ruling_count": str(expected_counts["rulings"]),
    }
    try:
        uri = database.resolve(strict=True).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise GeneratedOwnerCacheError("pinned card database failed quick_check")

            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
            mismatches = [field for field, value in expected_metadata.items() if metadata.get(field) != value]
            if mismatches:
                raise GeneratedOwnerCacheError(
                    "pinned card database metadata differs from the rules manifest: " + ", ".join(mismatches)
                )

            schema_rows = connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_schema "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall()
            counts = {
                name: connection.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
                for kind, name, _, _ in schema_rows
                if kind == "table"
            }
    except (OSError, sqlite3.Error) as exc:
        raise GeneratedOwnerCacheError("pinned card database is unavailable or invalid") from exc

    if counts.get("cards") != expected_counts["oracle_cards"] or counts.get("rulings") != expected_counts["rulings"]:
        raise GeneratedOwnerCacheError("pinned card database row counts differ from the rules manifest")
    return canonical_json_fingerprint(
        DATABASE_IDENTITY_ALGORITHM,
        {
            "builder_inputs": database_builder_input_fingerprint(root=root),
            "snapshot": snapshot,
            "schema": schema_rows,
            "row_counts": counts,
        },
    )
