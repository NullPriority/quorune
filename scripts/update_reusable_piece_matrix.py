from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import sys
import zlib


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorune.card_programs.commands import runtime_component_status
from quorune.reusable_pieces import (
    build_reusable_piece_artifacts,
    build_reusable_piece_delta,
    load_json,
    load_reusable_piece_policy,
    render_complex_card_benchmark_markdown,
    render_reusable_piece_delta_markdown,
    render_reusable_piece_matrix_markdown,
    validate_reusable_piece_artifacts,
)
from quorune.rule_conformance import discover_unittest_ids
from quorune.rules.capabilities import (
    load_default_capability_registry,
)
from quorune.util import stable_json
from scripts.validate_python_runtime import require_supported_python


MATRIX_OUTPUT = ROOT / "coverage" / "reusable-piece-matrix.json.gz"
MATRIX_MARKDOWN = ROOT / "coverage" / "reusable-piece-matrix.md"
CARD_INDEX_OUTPUT = (
    ROOT / "coverage" / "reusable-piece-card-index.json.gz"
)
INTERACTIONS_OUTPUT = (
    ROOT / "coverage" / "reusable-piece-interactions.json.gz"
)
DELTA_OUTPUT = ROOT / "coverage" / "reusable-piece-delta.json"
DELTA_MARKDOWN = ROOT / "coverage" / "reusable-piece-delta.md"
COMPLEX_OUTPUT = ROOT / "coverage" / "complex-card-composition.json"
COMPLEX_MARKDOWN = ROOT / "coverage" / "complex-card-composition.md"
BASELINE_OUTPUT = ROOT / "coverage" / "program-baseline.json"

FRONTIER_INPUT = ROOT / "coverage" / "card-unlock-frontier.json.gz"
CAPABILITY_INPUT = (
    ROOT / "quorune" / "rules" / "capability-registry.json"
)
MECHANICS_INPUT = ROOT / "mechanics" / "registry.json"
RULES_INPUT = ROOT / "rules" / "rule-index.json"
ORACLE_INPUT = ROOT / "coverage" / "oracle-coverage-commander.json"
PROGRAM_INPUT = (
    ROOT / "coverage" / "card-program-coverage-commander.json"
)
ARCHITECTURE_INPUT = ROOT / "platform" / "architecture-guard-baseline.json"
PLATFORM_INPUT = ROOT / "coverage" / "platform-readiness.json"
INTERACTION_EVIDENCE_INPUT = (
    ROOT / "platform" / "reusable-piece-interaction-evidence.json"
)


def _canonical_gzip(payload: bytes) -> bytes:
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


def _canonical_json_bytes(value: dict) -> bytes:
    return (stable_json(value) + "\n").encode("utf-8")


def _hash(value: dict) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def _capability_registry() -> dict:
    registry = load_default_capability_registry()
    raw = load_json(CAPABILITY_INPUT)
    raw["capabilities"] = registry.capabilities()
    return raw


def _ruling_counts(db_path: Path | None) -> dict[str, int]:
    if db_path is None:
        return {}
    with sqlite3.connect(db_path) as database:
        return {
            str(oracle_id): int(count)
            for oracle_id, count in database.execute(
                "SELECT oracle_id, COUNT(*) FROM rulings GROUP BY oracle_id"
            )
        }


def _build(
    *,
    db_path: Path | None,
    baseline: dict | None,
) -> dict[str, dict]:
    return build_reusable_piece_artifacts(
        frontier=load_json(FRONTIER_INPUT),
        capability_registry=_capability_registry(),
        mechanics_registry=load_json(MECHANICS_INPUT),
        runtime_status=runtime_component_status("commander_review"),
        rules_index=load_json(RULES_INPUT),
        oracle_coverage=load_json(ORACLE_INPUT),
        program_coverage=load_json(PROGRAM_INPUT),
        architecture_audit=load_json(ARCHITECTURE_INPUT),
        platform_status=load_json(PLATFORM_INPUT),
        policy=load_reusable_piece_policy(ROOT),
        interaction_evidence=load_json(INTERACTION_EVIDENCE_INPUT),
        known_test_ids={
            test_id.rsplit(".", 1)[-1]
            for test_id in discover_unittest_ids(ROOT)
        },
        baseline=baseline,
        ruling_counts=_ruling_counts(db_path),
    )


def _load_tracked() -> dict[str, dict]:
    return {
        "matrix": load_json(MATRIX_OUTPUT),
        "card_index": load_json(CARD_INDEX_OUTPUT),
        "interactions": load_json(INTERACTIONS_OUTPUT),
        "complex_cards": load_json(COMPLEX_OUTPUT),
        "baseline": load_json(BASELINE_OUTPUT),
        "delta": load_json(DELTA_OUTPUT),
    }


def _expected_delta(artifacts: dict[str, dict]) -> dict:
    return build_reusable_piece_delta(
        artifacts["matrix"],
        artifacts["interactions"],
        artifacts["baseline"],
        oracle_coverage=load_json(ORACLE_INPUT),
        program_coverage=load_json(PROGRAM_INPUT),
        architecture_audit=load_json(ARCHITECTURE_INPUT),
        policy=load_reusable_piece_policy(ROOT),
    )


def _write_json(path: Path, value: dict) -> None:
    path.write_bytes(_canonical_json_bytes(value))


def _write_artifacts(artifacts: dict[str, dict]) -> None:
    policy = load_reusable_piece_policy(ROOT)
    validate_reusable_piece_artifacts(artifacts, policy=policy)
    MATRIX_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    MATRIX_OUTPUT.write_bytes(
        _canonical_gzip(_canonical_json_bytes(artifacts["matrix"]))
    )
    CARD_INDEX_OUTPUT.write_bytes(
        _canonical_gzip(_canonical_json_bytes(artifacts["card_index"]))
    )
    INTERACTIONS_OUTPUT.write_bytes(
        _canonical_gzip(_canonical_json_bytes(artifacts["interactions"]))
    )
    _write_json(DELTA_OUTPUT, artifacts["delta"])
    _write_json(COMPLEX_OUTPUT, artifacts["complex_cards"])
    if not BASELINE_OUTPUT.exists():
        _write_json(BASELINE_OUTPUT, artifacts["baseline"])
    MATRIX_MARKDOWN.write_text(
        render_reusable_piece_matrix_markdown(
            artifacts["matrix"], policy=policy
        ),
        encoding="utf-8",
        newline="\n",
    )
    DELTA_MARKDOWN.write_text(
        render_reusable_piece_delta_markdown(artifacts["delta"]),
        encoding="utf-8",
        newline="\n",
    )
    COMPLEX_MARKDOWN.write_text(
        render_complex_card_benchmark_markdown(
            artifacts["complex_cards"], matrix=artifacts["matrix"]
        ),
        encoding="utf-8",
        newline="\n",
    )


def _check_canonical(artifacts: dict[str, dict]) -> None:
    policy = load_reusable_piece_policy(ROOT)
    validate_reusable_piece_artifacts(artifacts, policy=policy)
    compressed = {
        MATRIX_OUTPUT: artifacts["matrix"],
        CARD_INDEX_OUTPUT: artifacts["card_index"],
        INTERACTIONS_OUTPUT: artifacts["interactions"],
    }
    for path, value in compressed.items():
        expected = _canonical_gzip(_canonical_json_bytes(value))
        if path.read_bytes() != expected:
            raise ValueError(f"Reusable-piece artifact is not canonical: {path}")
    for path, value in (
        (DELTA_OUTPUT, artifacts["delta"]),
        (COMPLEX_OUTPUT, artifacts["complex_cards"]),
        (BASELINE_OUTPUT, artifacts["baseline"]),
    ):
        if path.read_bytes() != _canonical_json_bytes(value):
            raise ValueError(f"Reusable-piece artifact is not canonical: {path}")
    expected_markdown = {
        MATRIX_MARKDOWN: render_reusable_piece_matrix_markdown(
            artifacts["matrix"], policy=policy
        ),
        DELTA_MARKDOWN: render_reusable_piece_delta_markdown(
            artifacts["delta"]
        ),
        COMPLEX_MARKDOWN: render_complex_card_benchmark_markdown(
            artifacts["complex_cards"], matrix=artifacts["matrix"]
        ),
    }
    for path, expected in expected_markdown.items():
        if path.read_text(encoding="utf-8") != expected:
            raise ValueError(f"Reusable-piece Markdown is stale: {path}")


def _check_freshness(
    artifacts: dict[str, dict], *, check_derived: bool = True
) -> None:
    matrix = artifacts["matrix"]
    inputs = matrix["input_fingerprints"]
    frontier = load_json(FRONTIER_INPUT)
    runtime = runtime_component_status("commander_review")
    policy = load_reusable_piece_policy(ROOT)
    expected = {
        "frontier": frontier["fingerprint"],
        "capability_registry": runtime["capability_registry_fingerprint"],
        "capability_evidence": runtime["capability_evidence_fingerprint"],
        "mechanics_registry": _hash(load_json(MECHANICS_INPUT)),
        "semantic_handler_registry": runtime[
            "semantic_handler_registry_fingerprint"
        ],
        "runtime_component_registry": runtime[
            "runtime_component_registry_fingerprint"
        ],
        "rules_index": _hash(load_json(RULES_INPUT)),
        "oracle_coverage": _hash(load_json(ORACLE_INPUT)),
        "program_coverage": _hash(load_json(PROGRAM_INPUT)),
        "policy": _hash(policy),
        "interaction_evidence": _hash(
            load_json(INTERACTION_EVIDENCE_INPUT)
        ),
    }
    if inputs != expected:
        raise ValueError("Reusable-piece matrix input fingerprints are stale")
    platform = load_json(PLATFORM_INPUT)
    expected_snapshot = {
        key: dict(value)
        for key, value in platform["snapshots"].items()
        if key in {"comprehensive_rules", "oracle", "rulings"}
    }
    if matrix["snapshot"] != expected_snapshot:
        raise ValueError("Reusable-piece matrix pinned snapshot is stale")
    if artifacts["card_index"]["cards_considered"] != frontier[
        "cards_considered"
    ]:
        raise ValueError("Reusable-piece card index corpus count is stale")
    if check_derived and artifacts["delta"] != _expected_delta(artifacts):
        raise ValueError("Reusable-piece delta is stale")


def main() -> int:
    require_supported_python()
    parser = argparse.ArgumentParser(
        description="Generate the pinned reusable rules-piece inventory"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--refresh-derived", action="store_true")
    mode.add_argument("--validate-policy", action="store_true")
    parser.add_argument("--db", type=Path)
    args = parser.parse_args()
    if args.validate_policy:
        load_reusable_piece_policy(ROOT)
        return 0
    if args.refresh_derived:
        artifacts = _load_tracked()
        _check_canonical(artifacts)
        _check_freshness(artifacts, check_derived=False)
        delta = _expected_delta(artifacts)
        _write_json(DELTA_OUTPUT, delta)
        DELTA_MARKDOWN.write_text(
            render_reusable_piece_delta_markdown(delta),
            encoding="utf-8",
            newline="\n",
        )
        return 0
    if args.write:
        if args.db is None:
            parser.error("--write requires --db for official-ruling counts")
        baseline = load_json(BASELINE_OUTPUT) if BASELINE_OUTPUT.exists() else None
        artifacts = _build(db_path=args.db, baseline=baseline)
        _write_artifacts(artifacts)
        return 0
    artifacts = _load_tracked()
    _check_canonical(artifacts)
    _check_freshness(artifacts)
    if args.db is not None:
        rebuilt = _build(db_path=args.db, baseline=artifacts["baseline"])
        if rebuilt != artifacts:
            raise ValueError(
                "Reusable-piece artifacts do not match the pinned database"
            )
    print(
        stable_json(
            {
                "ok": True,
                "pieces": artifacts["matrix"]["summary"]["piece_count"],
                "cards": artifacts["card_index"]["cards_considered"],
                "fingerprint": artifacts["matrix"]["fingerprint"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
