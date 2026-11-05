from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import struct
import sys
import zlib


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorune.carddb import CardDatabase
from quorune.frontier_storage import pack_frontier_document, unpack_frontier_document
from quorune.compiler.unlock_frontier import (
    build_card_unlock_frontier,
    render_card_unlock_frontier_markdown,
    validate_card_unlock_frontier,
)
from quorune.mechanic_contracts import load_mechanic_contracts
from quorune.rules.capabilities import (
    load_default_capability_registry,
)
from quorune.semantics import SemanticRegistry
from quorune.util import stable_json
from scripts.validate_python_runtime import require_supported_python


JSON_GZIP_OUTPUT = ROOT / "coverage" / "card-unlock-frontier.json.gz"
MARKDOWN_OUTPUT = ROOT / "coverage" / "card-unlock-frontier.md"
ORACLE_COVERAGE = ROOT / "coverage" / "oracle-coverage-commander.json"
CARD_PROGRAM_COVERAGE = (
    ROOT / "coverage" / "card-program-coverage-commander.json"
)


def _contracts() -> list[dict]:
    manifest = json.loads(
        (ROOT / "rules" / "manifest.json").read_text(encoding="utf-8")
    )
    rules = json.loads(
        (ROOT / "rules" / "rule-index.json").read_text(encoding="utf-8")
    )
    return load_mechanic_contracts(
        ROOT,
        expected_effective_date=manifest["effective_date"],
        expected_source_sha256=manifest["source_sha256"],
        known_rule_ids={row["rule_id"] for row in rules["rules"]},
    )


def _build(db_path: Path, *, limit: int | None) -> dict:
    capabilities = load_default_capability_registry()
    with CardDatabase(db_path) as db:
        return build_card_unlock_frontier(
            db,
            registry=SemanticRegistry(),
            capabilities=capabilities,
            mechanic_contracts=_contracts(),
            profile="commander_review",
            limit=limit,
        )


def _snapshot_freshness(report: dict) -> None:
    oracle = json.loads(ORACLE_COVERAGE.read_text(encoding="utf-8"))
    programs = json.loads(
        CARD_PROGRAM_COVERAGE.read_text(encoding="utf-8")
    )
    snapshot = report["card_data_snapshot"]
    for field in (
        "oracle_source_sha256",
        "rulings_source_sha256",
        "scryfall_oracle_updated_at",
        "scryfall_rulings_updated_at",
    ):
        if snapshot.get(field) != oracle["card_data_snapshot"].get(field):
            raise ValueError(
                f"Card-unlock frontier has stale card snapshot field {field}"
            )
    if report["cards_considered"] != oracle["total_oracle_ids"]:
        raise ValueError("Card-unlock frontier Oracle card count is stale")
    expected_program_states = programs["status_counts"]
    if report["card_program_status_counts"] != expected_program_states:
        raise ValueError("Card-unlock frontier CardProgram states are stale")
    if report["hard_construction_failures"] != programs["failures"]:
        raise ValueError("Card-unlock frontier hard failures are stale")


def _canonical_gzip(payload: bytes) -> bytes:
    """Return a platform-independent RFC 1952 stream with a zero timestamp."""

    compressor = zlib.compressobj(
        level=9,
        method=zlib.DEFLATED,
        wbits=-zlib.MAX_WBITS,
    )
    body = compressor.compress(payload) + compressor.flush()
    header = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff"
    trailer = struct.pack(
        "<II",
        zlib.crc32(payload) & 0xFFFFFFFF,
        len(payload) & 0xFFFFFFFF,
    )
    return header + body + trailer


def _canonical_report_bytes(report: dict) -> bytes:
    return (stable_json(pack_frontier_document(report)) + "\n").encode("utf-8")


def _load_tracked() -> tuple[dict, bytes, str]:
    if not JSON_GZIP_OUTPUT.exists() or not MARKDOWN_OUTPUT.exists():
        raise ValueError("Card-unlock frontier artifacts are missing")
    try:
        report = unpack_frontier_document(json.loads(
            gzip.decompress(JSON_GZIP_OUTPUT.read_bytes()).decode("utf-8")
        ))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Card-unlock frontier gzip JSON is invalid") from exc
    validate_card_unlock_frontier(report)
    expected_gzip = _canonical_gzip(_canonical_report_bytes(report))
    expected_markdown = render_card_unlock_frontier_markdown(report)
    return report, expected_gzip, expected_markdown


def main() -> int:
    require_supported_python()
    parser = argparse.ArgumentParser(
        description="Generate the pinned Commander card-unlock frontier"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--repack", action="store_true", help="Losslessly repack a validated current frontier without compiling cards")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.repack:
        report, encoded, markdown = _load_tracked()
        _snapshot_freshness(report)
        JSON_GZIP_OUTPUT.write_bytes(encoded)
        MARKDOWN_OUTPUT.write_text(markdown, encoding="utf-8", newline="\n")
        return 0
    if args.write:
        if args.db is None:
            parser.error("--write requires --db")
        report = _build(args.db, limit=args.limit)
        JSON_GZIP_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        JSON_GZIP_OUTPUT.write_bytes(
            _canonical_gzip(_canonical_report_bytes(report))
        )
        MARKDOWN_OUTPUT.write_text(
            render_card_unlock_frontier_markdown(report),
            encoding="utf-8",
            newline="\n",
        )
        return 0
    report, expected_gzip, expected_markdown = _load_tracked()
    _snapshot_freshness(report)
    if JSON_GZIP_OUTPUT.read_bytes() != expected_gzip:
        raise ValueError("Card-unlock frontier gzip JSON is not canonical")
    if MARKDOWN_OUTPUT.read_text(encoding="utf-8") != expected_markdown:
        raise ValueError("Card-unlock frontier Markdown is stale")
    if args.db is not None:
        regenerated = _build(args.db, limit=args.limit)
        if regenerated != report:
            raise ValueError("Card-unlock frontier does not match the database")
    print(
        stable_json(
            {
                "ok": True,
                "cards_considered": report["cards_considered"],
                "fingerprint": report["fingerprint"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
