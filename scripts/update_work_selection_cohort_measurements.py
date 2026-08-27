from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorune.carddb import CardDatabase
from quorune.util import stable_json
from quorune.work_selection_bundles import bundle_measurement_fingerprint
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
    return build_work_selection_cohort_measurements(
        frontier=frontier,
        bundle_policies=bundles,
        cards_by_oracle_id=records,
        coverage=coverage,
        cohort_fingerprints=fingerprints,
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
    if args.db is not None and value != _build(args.db):
        raise ValueError(
            "Work-selection cohort measurements do not match the pinned database"
        )
    print(stable_json({"ok": True, "fingerprint": value["fingerprint"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
