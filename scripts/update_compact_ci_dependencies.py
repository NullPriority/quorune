from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorune.util import stable_json
from scripts.compact_ci_report import (
    COMPACT_CI_REPORT_MARKDOWN_PATH,
    COMPACT_CI_REPORT_PATH,
    build_dependency_report,
    dependency_report_bytes,
    report_markdown,
)


JSON_OUTPUT = ROOT / COMPACT_CI_REPORT_PATH
MARKDOWN_OUTPUT = ROOT / COMPACT_CI_REPORT_MARKDOWN_PATH


def render() -> tuple[bytes, str]:
    report = build_dependency_report(root=ROOT)
    if not report["closed"]:
        raise ValueError(
            "Compact CI card/deck dependency coverage is open; run "
            "scripts/build_test_database.py validate-ci-dependencies for details"
        )
    return dependency_report_bytes(report), report_markdown(report)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or verify compact-CI card dependency closure"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    compressed, markdown = render()
    stale = []
    if not JSON_OUTPUT.is_file() or JSON_OUTPUT.read_bytes() != compressed:
        stale.append(JSON_OUTPUT.relative_to(ROOT).as_posix())
    if not MARKDOWN_OUTPUT.is_file() or MARKDOWN_OUTPUT.read_text(encoding="utf-8") != markdown:
        stale.append(MARKDOWN_OUTPUT.relative_to(ROOT).as_posix())
    if args.check:
        if stale:
            print("Stale compact-CI dependency outputs: " + ", ".join(stale))
            return 1
        print(stable_json({"closed": True, "stale": []}))
        return 0
    JSON_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUTPUT.write_bytes(compressed)
    MARKDOWN_OUTPUT.write_text(markdown, encoding="utf-8", newline="\n")
    print(stable_json({"closed": True, "outputs": sorted(stale)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
