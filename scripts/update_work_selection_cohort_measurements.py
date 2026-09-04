from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorune.carddb import CardDatabase
from quorune.util import stable_json
from quorune.work_selection_bundles import bundle_measurement_fingerprint
from quorune.work_selection_common import stable_hash
from quorune.work_selection_evidence import (
    validate_work_selection_cohort_measurements,
)
from scripts.work_selection_cohort_measurements import (
    build_work_selection_cohort_measurements,
)
from scripts.validate_python_runtime import require_supported_python


OUTPUT = ROOT / "coverage" / "work-selection-cohort-measurements.json"
FRONTIER = ROOT / "coverage" / "card-unlock-frontier.json.gz"
POLICY = ROOT / "platform" / "rules-subsystems.json"
HARVEST_HISTORY = ROOT / "coverage" / "harvest-outcome-history.json"


def _inputs() -> tuple[dict, dict, list[dict], dict[str, str]]:
    frontier = json.loads(gzip.decompress(FRONTIER.read_bytes()))
    catalog = json.loads(POLICY.read_text(encoding="utf-8"))
    coverage = catalog["work_selection"]["coverage_family"]
    bundles = list(coverage["candidate_bundles"])
    fingerprints = {
        str(bundle["bundle_id"]): bundle_measurement_fingerprint(frontier, bundle)
        for bundle in bundles
        if bundle.get("measurement_probe_id") is not None
    }
    return frontier, coverage, bundles, fingerprints


def _build(database: Path) -> dict:
    frontier, coverage, bundles, fingerprints = _inputs()
    with CardDatabase(database) as cards:
        records = {
            card.oracle_id: card
            for card in cards.iter_cards(commander_legal_only=True)
        }
    transition_measurements = _transition_measurements(
        records=records,
        coverage=coverage,
        bundles=bundles,
    )
    value = build_work_selection_cohort_measurements(
        frontier=frontier,
        bundle_policies=bundles,
        cards_by_oracle_id=records,
        coverage=coverage,
        cohort_fingerprints=fingerprints,
        transition_measurements=transition_measurements,
    )
    validate_work_selection_cohort_measurements(
        value,
        frontier=frontier,
        bundle_policies=bundles,
        cohort_fingerprints=fingerprints,
        coverage=coverage,
    )
    _validate_transition_binding(value)
    return value


def _source_checkpoint_frontier() -> dict:
    completed = subprocess.run(
        ["git", "show", "HEAD:coverage/card-unlock-frontier.json.gz"],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise ValueError(
            "Cannot read the source-checkpoint card frontier for transition measurement"
        )
    try:
        value = json.loads(gzip.decompress(completed.stdout))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "Source-checkpoint card frontier is malformed"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError("Source-checkpoint card frontier must be an object")
    return value


def _preserved_transition_measurement(
    *, transition_id: str, measurement_id: str
) -> dict | None:
    try:
        value = json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    for row in value.get("transition_measurements", []):
        measurement = row.get("measurement") if isinstance(row, dict) else None
        if (
            isinstance(measurement, dict)
            and row.get("transition_id") == transition_id
            and measurement.get("measurement_id") == measurement_id
        ):
            return dict(row)
    return None


def _preserved_transition_is_current(
    preserved: dict | None,
    *,
    frontier_fingerprint: str,
    oracle_source_sha256: str,
    cohort_fingerprint: str,
    completed_receipt_fingerprints: frozenset[str] = frozenset(),
) -> bool:
    if preserved is None:
        return False
    measurement = preserved.get("measurement")
    if (
        not isinstance(measurement, dict)
        or preserved.get("oracle_source_sha256") != oracle_source_sha256
    ):
        return False
    return bool(
        (
            preserved.get("frontier_fingerprint")
            == frontier_fingerprint
            and measurement.get("cohort_fingerprint")
            == cohort_fingerprint
        )
        or preserved.get("receipt_fingerprint")
        in completed_receipt_fingerprints
    )


def _completed_transition_measurement_receipts(
    transition_id: str,
) -> frozenset[str]:
    try:
        value = json.loads(HARVEST_HISTORY.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return frozenset()
    return frozenset(
        str(entry.get("measurement_receipt_fingerprint") or "")
        for entry in value.get("entries", ())
        if isinstance(entry, dict)
        and entry.get("transition_id") == transition_id
        and str(entry.get("measurement_receipt_fingerprint") or "")
    )


def _transition_measurements(
    *, records: dict, coverage: dict, bundles: list[dict]
) -> list[dict]:
    catalog = json.loads(POLICY.read_text(encoding="utf-8"))
    declaration = catalog["work_selection"].get(
        "semantic_transition_declaration"
    )
    if not isinstance(declaration, dict):
        return []
    transition_id = str(declaration.get("transition_id") or "")
    measurement_id = declaration.get("measurement_id")
    if not transition_id or not isinstance(measurement_id, str):
        return []
    bundle_id = str(declaration.get("bundle_id") or "")
    bundle = next(
        (row for row in bundles if row.get("bundle_id") == bundle_id), None
    )
    if bundle is None or measurement_id != "measurement:" + bundle_id.split(
        ":", 1
    )[-1]:
        raise ValueError(
            "Semantic transition measurement does not identify its candidate bundle"
        )
    frontier = _source_checkpoint_frontier()
    fingerprints = {
        bundle_id: bundle_measurement_fingerprint(frontier, bundle)
    }
    snapshot = frontier.get("card_data_snapshot")
    oracle_source_sha256 = (
        str(snapshot.get("oracle_source_sha256") or "")
        if isinstance(snapshot, dict)
        else ""
    )
    preserved = _preserved_transition_measurement(
        transition_id=transition_id,
        measurement_id=measurement_id,
    )
    if _preserved_transition_is_current(
        preserved,
        frontier_fingerprint=str(frontier.get("fingerprint") or ""),
        oracle_source_sha256=oracle_source_sha256,
        cohort_fingerprint=fingerprints[bundle_id],
        completed_receipt_fingerprints=(
            _completed_transition_measurement_receipts(transition_id)
        ),
    ):
        return [preserved]
    measured = build_work_selection_cohort_measurements(
        frontier=frontier,
        bundle_policies=[bundle],
        cards_by_oracle_id=records,
        coverage=coverage,
        cohort_fingerprints=fingerprints,
    )["measurements"][0]
    if (
        measured.get("decision") != "bounded_executable"
        or int(measured.get("complete_card_gain") or 0) <= 0
    ):
        raise ValueError(
            "Semantic transition requires a generated positive complete-card "
            "cohort lower bound"
        )
    receipt = {
        "transition_id": transition_id,
        "frontier_fingerprint": str(frontier.get("fingerprint") or ""),
        "oracle_source_sha256": oracle_source_sha256,
        "measurement": measured,
    }
    receipt["receipt_fingerprint"] = stable_hash(receipt)
    return [receipt]


def _validate_transition_binding(value: dict) -> None:
    catalog = json.loads(POLICY.read_text(encoding="utf-8"))
    declaration = catalog["work_selection"].get(
        "semantic_transition_declaration"
    )
    measurement_id = (
        declaration.get("measurement_id")
        if isinstance(declaration, dict)
        else None
    )
    receipts = value.get("transition_measurements", [])
    if measurement_id is None:
        if receipts:
            raise ValueError(
                "Cohort artifact retains a transition measurement without a declaration"
            )
        return
    matching = [
        row
        for row in receipts
        if row.get("transition_id") == declaration.get("transition_id")
        and row.get("measurement", {}).get("measurement_id") == measurement_id
        and row.get("measurement", {}).get("bundle_id")
        == declaration.get("bundle_id")
    ]
    if len(matching) != 1 or len(receipts) != 1:
        raise ValueError(
            "Cohort artifact lacks the declared transition measurement"
        )


def main() -> int:
    require_supported_python()
    parser = argparse.ArgumentParser(
        description="Generate current bounded work-selection cohort measurements"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--db", type=Path)
    args = parser.parse_args()
    if args.write:
        if args.db is None:
            parser.error("--write requires --db")
        OUTPUT.write_text(
            stable_json(_build(args.db)) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return 0
    if not OUTPUT.is_file():
        raise ValueError("Work-selection cohort measurement artifact is missing")
    value = json.loads(OUTPUT.read_text(encoding="utf-8"))
    frontier, _coverage, bundles, fingerprints = _inputs()
    validate_work_selection_cohort_measurements(
        value,
        frontier=frontier,
        bundle_policies=bundles,
        cohort_fingerprints=fingerprints,
        coverage=_coverage,
    )
    _validate_transition_binding(value)
    if args.db is not None and value != _build(args.db):
        raise ValueError(
            "Work-selection cohort measurements do not match the pinned database"
        )
    print(stable_json({"ok": True, "fingerprint": value["fingerprint"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
