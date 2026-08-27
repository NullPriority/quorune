from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verify_nightly_ci import (
    NightlyCertificationError,
    validate_results,
)


EXPECTED_DEPENDENCIES = frozenset(
    {"browser", "governance", "package", "plan", "python"}
)


def validate_dependencies(needs: Mapping) -> None:
    if set(needs) != EXPECTED_DEPENDENCIES:
        raise NightlyCertificationError(
            "Main broad dependency graph is incomplete or unreviewed"
        )
    failed = sorted(
        name
        for name in EXPECTED_DEPENDENCIES
        if not isinstance(needs.get(name), Mapping)
        or needs[name].get("result") != "success"
    )
    if failed:
        raise NightlyCertificationError(
            f"Main broad dependencies did not all pass: {failed}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed exact-main broad regression certification"
    )
    parser.add_argument("--results-dir", required=True)
    args = parser.parse_args()
    raw = os.environ.get("CI_MAIN_BROAD_NEEDS_JSON")
    if not raw:
        print("CI_MAIN_BROAD_NEEDS_JSON is required")
        return 1
    try:
        needs = json.loads(raw)
        if not isinstance(needs, dict):
            raise NightlyCertificationError(
                "CI_MAIN_BROAD_NEEDS_JSON must contain an object"
            )
        validate_dependencies(needs)
        summary = validate_results(Path(args.results_dir))
    except (
        json.JSONDecodeError,
        NightlyCertificationError,
        OSError,
        ValueError,
    ) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, **summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
