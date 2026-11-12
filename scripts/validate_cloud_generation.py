"""Check cloud owner coverage, dependency order, and runner bounds before work."""
from __future__ import annotations

import argparse
from itertools import combinations
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.generated_artifacts import load_manifest
from scripts.validate_python_runtime import require_supported_python


CLOUD_RUNNER_BUDGET = 6


def _literals(raw: str) -> tuple[str, ...]:
    text = raw.strip()
    if text.startswith("[") and text.endswith("]"):
        values = tuple(value.strip() for value in text[1:-1].split(","))
    elif text.startswith("- "):
        values = tuple(line.strip().removeprefix("- ") for line in text.splitlines())
    else:
        values = (text,)
    if (not values or len(values) != len(set(values))
            or any(re.fullmatch(r"[a-z][a-z0-9_-]*", value) is None for value in values)):
        raise ValueError("Cloud workflow requires unique literal job and owner lists")
    return values


def _job_schedule(text: str):
    if "\njobs:\n" not in text:
        raise ValueError("Cloud workflow has no jobs section")
    source = text.split("\njobs:\n", 1)[1]
    headers = tuple(re.finditer(r"^  ([a-z][a-z0-9_]*):\n", source, re.MULTILINE))
    jobs, producers, weights = {}, {}, {}
    for index, header in enumerate(headers):
        name = header[1]
        if name in jobs:
            raise ValueError(f"Duplicate cloud workflow job: {name}")
        body = source[header.end():headers[index + 1].start() if index + 1 < len(headers) else len(source)]
        needs = re.search(r"^    needs:([^\n]*(?:\n      - [^\n]+)*)", body, re.MULTILINE)
        jobs[name] = _literals(needs[1]) if needs is not None else ()
        matrix = re.search(r"^        owner:([^\n]*(?:\n          - [^\n]+)*)", body, re.MULTILINE)
        values = _literals(matrix[1]) if matrix is not None else ()
        parallel = re.search(r"^      max-parallel: ([1-9]\d*)$", body, re.MULTILINE)
        if "      matrix:" in body and (not values or parallel is None):
            raise ValueError(f"Cloud owner matrix needs a literal owner list and runner bound: {name}")
        weights[name] = min(len(values), int(parallel[1])) if values else 1
        flat = " ".join(body.split())
        commands = tuple(re.finditer(
            r'python scripts/cloud_generated_artifacts\.py run-owner --owner ("[^"]+"|[^ ]+)', flat))
        if len(commands) != flat.count("python scripts/cloud_generated_artifacts.py run-owner"):
            raise ValueError(f"Unrecognized cloud owner command: {name}")
        for ordinal, command in enumerate(commands):
            argument = command[1].strip('"')
            owners = values if argument == "${{ matrix.owner }}" else _literals(argument)
            if not owners:
                raise ValueError(f"Cloud owner command has no matrix values: {name}")
            for owner in owners:
                if owner in producers:
                    raise ValueError(f"Duplicate cloud generated owner producer: {owner}")
                producers[owner] = (name, ordinal)
    if not jobs or len(jobs) > 20:
        raise ValueError("Cloud job graph requires a bounded concurrency review")
    return jobs, producers, weights


def _ancestors(jobs):
    result, visiting = {}, set()

    def visit(name):
        if name in visiting:
            raise ValueError(f"Cloud workflow dependency cycle: {name}")
        if name not in jobs:
            raise ValueError(f"Unknown cloud workflow dependency: {name}")
        if name not in result:
            visiting.add(name)
            result[name] = set()
            for dependency in jobs[name]:
                result[name].update((dependency, *visit(dependency)))
            visiting.remove(name)
        return result[name]

    for name in jobs:
        visit(name)
    return result


def validate_cloud_schedule(*, root: Path = ROOT, workflow_text: str | None = None) -> dict:
    text = workflow_text if workflow_text is not None else (
        root / ".github/workflows/generated-artifacts.yml").read_text(encoding="utf-8")
    specs = {spec.id: spec for spec in load_manifest(root=root)}
    jobs, producers, weights = _job_schedule(text)
    required = {spec.id for spec in specs.values() if spec.reuse_policy == "safe"}
    missing, unknown = required - producers.keys(), producers.keys() - specs.keys()
    if missing or unknown:
        raise ValueError(f"Cloud generated owner inventory mismatch: missing={sorted(missing)} unknown={sorted(unknown)}")
    ancestors = _ancestors(jobs)
    for owner, (job, ordinal) in producers.items():
        for dependency in specs[owner].depends_on:
            if dependency not in producers:
                continue
            producer, position = producers[dependency]
            if (producer == job and position >= ordinal) or (producer != job and producer not in ancestors[job]):
                raise ValueError(f"Cloud generated owner dependency is unordered: {dependency} before {owner}")
    peak = max(sum(weights[name] for name in group)
        for count in range(1, len(jobs) + 1) for group in combinations(jobs, count)
        if all(left not in ancestors[right] and right not in ancestors[left]
            for left, right in combinations(group, 2)))
    if peak > CLOUD_RUNNER_BUDGET:
        raise ValueError(f"Cloud runner concurrency exceeds its checked budget: {peak} > {CLOUD_RUNNER_BUDGET}")
    return {"ok": True, "owners": sorted(producers), "owner_jobs": {
        owner: job for owner, (job, _ordinal) in sorted(producers.items())},
        "peak_parallel_runners": peak, "runner_budget": CLOUD_RUNNER_BUDGET}


def main() -> int:
    require_supported_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", required=True)
    parser.parse_args()
    try:
        result = validate_cloud_schedule()
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
