from __future__ import annotations

"""Run the registered original-source grant measurement without a corpus census."""

import argparse
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quorune.carddb import CardDatabase
from quorune.work_selection_bundles import bundle_measurement_fingerprint
from scripts.typed_grant_carrier_measurement import typed_grant_carrier_measurement


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    frontier = json.loads(gzip.decompress((ROOT / "coverage/card-unlock-frontier.json.gz").read_bytes()))
    policy = json.loads((ROOT / "platform/rules-subsystems.json").read_text(encoding="utf-8"))["work_selection"]["coverage_family"]
    bundle = next(row for row in policy["candidate_bundles"] if row["bundle_id"] == "bundle:typed-layer-six-grant-carriers")
    diagnostic = []
    with CardDatabase(args.db) as database:
        records = {r.oracle_id: r for r in database.iter_cards(commander_legal_only=True)}
        result = typed_grant_carrier_measurement(
            frontier=frontier, bundle_id=bundle["bundle_id"], probe_id=bundle["measurement_probe_id"],
            cards_by_oracle_id=records, coverage=policy, cohort_fingerprint=bundle_measurement_fingerprint(frontier, bundle),
            database=database, diagnostic_rows=diagnostic,
        )
    args.output.write_text(json.dumps({"measurement": result, "original_cards": diagnostic},indent=2,ensure_ascii=True),encoding="utf-8")
    print(json.dumps(result,indent=2,ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
