from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorune.util import stable_json
from scripts.compact_ci_report import (
    COMPACT_CI_REPORT_PATH,
    build_dependency_report,
    report_markdown,
)


JSON_OUTPUT = ROOT / COMPACT_CI_REPORT_PATH
MARKDOWN_OUTPUT = JSON_OUTPUT.with_suffix(".md")


def render() -> dict[Path, str]:
    report = build_dependency_report(root=ROOT)
    if not report["closed"]:
        raise ValueError(
            "Compact CI card/deck dependency coverage is open; run "
            "scripts/build_test_database.py validate-ci-dependencies for details"
        )
    return {
        JSON_OUTPUT: stable_json(report) + "\n",
        MARKDOWN_OUTPUT: report_markdown(report),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or verify compact-CI card dependency closure"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    outputs = render()
    stale = [
        path.relative_to(ROOT).as_posix()
        for path, expected in outputs.items()
        if not path.is_file() or path.read_text(encoding="utf-8") != expected
    ]
    if args.check:
        if stale:
            print("Stale compact-CI dependency outputs: " + ", ".join(stale))
            return 1
        print(stable_json({"closed": True, "stale": []}))
        return 0
    for path, value in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    print(stable_json({"closed": True, "outputs": sorted(stale)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
