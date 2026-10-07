from __future__ import annotations

"""Run the canonical complete-program measurement for one entry-choice probe."""

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
from scripts.work_selection_cohort_measurements import build_work_selection_cohort_measurements


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frontier", type=Path, default=ROOT / "coverage/card-unlock-frontier.json.gz")
    args = parser.parse_args()
    frontier = json.loads(gzip.decompress(args.frontier.read_bytes()))
    policy = json.loads((ROOT / "platform/rules-subsystems.json").read_text(encoding="utf-8"))["work_selection"]["coverage_family"]
    bundle = next(row for row in policy["candidate_bundles"] if row["bundle_id"] == "bundle:persistent-entry-designations")
    fingerprint = bundle_measurement_fingerprint(frontier, bundle)
    with CardDatabase(args.db) as database:
        records = {record.oracle_id: record for record in database.iter_cards(commander_legal_only=True)}
        result = build_work_selection_cohort_measurements(frontier=frontier, bundle_policies=[bundle], cards_by_oracle_id=records, coverage=policy, cohort_fingerprints={bundle["bundle_id"]: fingerprint}, database=database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(stable_json(result) + "\n", encoding="utf-8", newline="\n")
    row = result["measurements"][0]
    print(stable_json({key: row[key] for key in ("complete_card_gain", "exact_ability_gain", "material_residual_reduction", "affected_commander_cards", "decision", "grants_gameplay_trust")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
