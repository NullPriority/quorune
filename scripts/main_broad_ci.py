from __future__ import annotations

import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.nightly_ci import nightly_python_matrix


MAIN_BROAD_PYTHON_MAX_PARALLEL = 12
MAIN_BROAD_FIXED_PARALLEL_JOBS = 6  # browser x3, package x2, governance
PUBLIC_JOB_CONCURRENCY_LIMIT = 20
PUBLIC_JOB_RECOVERY_HEADROOM = 2


def main_broad_concurrency_budget() -> dict[str, int]:
    peak = MAIN_BROAD_PYTHON_MAX_PARALLEL + MAIN_BROAD_FIXED_PARALLEL_JOBS
    headroom = PUBLIC_JOB_CONCURRENCY_LIMIT - peak
    if headroom < PUBLIC_JOB_RECOVERY_HEADROOM:
        raise ValueError("Main broad regression exceeds the public runner budget")
    return {
        "python_max_parallel": MAIN_BROAD_PYTHON_MAX_PARALLEL,
        "fixed_parallel_jobs": MAIN_BROAD_FIXED_PARALLEL_JOBS,
        "peak_jobs": peak,
        "headroom": headroom,
    }


def main() -> int:
    plan = {
        "python_matrix": nightly_python_matrix(),
        **main_broad_concurrency_budget(),
    }
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(
                "python_matrix="
                + json.dumps(plan["python_matrix"], separators=(",", ":"))
                + "\n"
            )
            stream.write(
                f"python_max_parallel={plan['python_max_parallel']}\n"
            )
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
