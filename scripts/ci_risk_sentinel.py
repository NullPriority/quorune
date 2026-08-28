from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ci_policy_delta import additive_selection_authority_paths


SELECTION_AUTHORITY_PATTERNS = (
    ".github/workflows/**",
    "platform/change-impact-policy.json",
    "platform/generated-artifacts.json",
    "platform/test-shards.json",
    "scripts/certification_receipt.py",
    "scripts/change_impact.py",
    "scripts/ci_plan.py",
    "scripts/ci_policy_delta.py",
    "scripts/ci_risk_sentinel.py",
    "scripts/compact_ci_dependencies.py",
    "scripts/finalize_generated.py",
    "scripts/generated_artifacts.py",
    "scripts/generated_owner_cache.py",
    "scripts/main_broad_ci.py",
    "scripts/main_health.py",
    "scripts/source_tree_fingerprint.py",
    "scripts/test_shards.py",
    "scripts/verify_ci_needs.py",
    "scripts/verify_main_broad_ci.py",
    "scripts/verify_windows_ci.py",
)


def requires_high_risk_gate(
    paths: tuple[str, ...],
    *,
    removed_paths: tuple[str, ...] = (),
    additive_selection_paths: tuple[str, ...] = (),
) -> tuple[str, ...]:
    additive = set(additive_selection_paths)
    reasons = {
        f"selection-authority:{path}"
        for path in paths
        if path not in additive
        if any(
            fnmatchcase(path, pattern)
            for pattern in SELECTION_AUTHORITY_PATTERNS
        )
    }
    reasons.update(f"removed:{path}" for path in removed_paths)
    return tuple(sorted(reasons))


def _github_base(event_path: str | None) -> str:
    path = event_path or os.environ.get("GITHUB_EVENT_PATH")
    if path:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        base = value.get("pull_request", {}).get("base", {}).get("sha")
        if isinstance(base, str) and base:
            return base
    return "origin/main"


def _diff_paths(root: Path, base: str, *, diff_filter: str | None = None) -> tuple[str, ...]:
    command = ["git", "diff", "--name-only"]
    if diff_filter:
        command.append(f"--diff-filter={diff_filter}")
    command.append(f"{base}...HEAD")
    result = subprocess.run(
        command,
        cwd=root,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "unable to inspect changed paths")
    return tuple(sorted({line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Independently force the complete gate for CI-selection changes"
    )
    parser.add_argument("--base")
    parser.add_argument("--event")
    parser.add_argument("--github-output")
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()
    root = Path(args.root).resolve()
    base = args.base or _github_base(args.event)
    paths = _diff_paths(root, base)
    removed = _diff_paths(root, base, diff_filter="DR")
    added = _diff_paths(root, base, diff_filter="A")
    additive, delta_reasons = additive_selection_authority_paths(
        root=root,
        base_ref=base,
        changed_paths=paths,
        added_paths=added,
    )
    reasons = tuple(
        sorted(
            set(
                requires_high_risk_gate(
                    paths,
                    removed_paths=removed,
                    additive_selection_paths=additive,
                )
            )
            | set(delta_reasons)
        )
    )
    if args.github_output:
        with Path(args.github_output).open(
            "a", encoding="utf-8", newline="\n"
        ) as stream:
            stream.write(f"force_high_risk={str(bool(reasons)).lower()}\n")
            stream.write(
                "additive_selection_paths_json="
                + json.dumps(additive, separators=(",", ":"))
                + "\n"
            )
    print(
        json.dumps(
            {
                "force_high_risk": bool(reasons),
                "additive_selection_paths": additive,
                "reasons": reasons,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
