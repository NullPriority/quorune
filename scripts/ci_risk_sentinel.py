from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.change_impact import changed_files, github_base


SELECTION_AUTHORITY_PATTERNS = (
    ".github/workflows/**",
    "platform/change-impact-policy.json",
    "platform/generated-artifacts.json",
    "platform/test-shards.json",
    "scripts/certification_receipt.py",
    "scripts/change_impact.py",
    "scripts/ci_plan.py",
    "scripts/ci_risk_sentinel.py",
    "scripts/compact_ci_dependencies.py",
    "scripts/finalize_generated.py",
    "scripts/main_broad_ci.py",
    "scripts/main_health.py",
    "scripts/source_tree_fingerprint.py",
    "scripts/test_shards.py",
    "scripts/verify_ci_needs.py",
    "scripts/verify_main_broad_ci.py",
    "scripts/verify_windows_ci.py",
)


def requires_high_risk_gate(
    paths: tuple[str, ...], *, removed_paths: tuple[str, ...] = ()
) -> tuple[str, ...]:
    reasons = {
        f"selection-authority:{path}"
        for path in paths
        if any(
            fnmatchcase(path, pattern)
            for pattern in SELECTION_AUTHORITY_PATTERNS
        )
    }
    reasons.update(f"removed:{path}" for path in removed_paths)
    return tuple(sorted(reasons))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Independently force the complete gate for CI-selection changes"
    )
    parser.add_argument("--base")
    parser.add_argument("--event")
    parser.add_argument("--github-output")
    args = parser.parse_args()
    base = args.base or github_base(args.event)
    paths = changed_files(base, include_worktree=False)
    removed = changed_files(
        base,
        include_worktree=False,
        diff_filter="DR",
    )
    reasons = requires_high_risk_gate(paths, removed_paths=removed)
    if args.github_output:
        with Path(args.github_output).open(
            "a", encoding="utf-8", newline="\n"
        ) as stream:
            stream.write(f"force_high_risk={str(bool(reasons)).lower()}\n")
    print(
        json.dumps(
            {"force_high_risk": bool(reasons), "reasons": reasons},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
