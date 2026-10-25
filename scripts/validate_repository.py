from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)

from scripts.validate_python_runtime import validate as validate_python_runtime

FORBIDDEN_TRACKED = (
    re.compile(r"^(?:run|runs|local|scratch|tmp)/", re.IGNORECASE),
    re.compile(r"^data/(?:bulk|cache|downloads)/", re.IGNORECASE),
    re.compile(r"^data/.*\.sqlite3(?:-.+)?$", re.IGNORECASE),
    re.compile(r"^(?:build|dist)/", re.IGNORECASE),
    re.compile(r"(?:^|/)\.arena\.lock$", re.IGNORECASE),
    re.compile(r"\.(?:whl|pem|key)$", re.IGNORECASE),
)
SECRET_PATTERNS = {
    "GitHub token": re.compile(
        rb"(?:gh[pousr]_[A-Za-z0-9_]{10,}|github_pat_[A-Za-z0-9_]{10,})"
    ),
    "OpenAI-style key": re.compile(rb"sk-[A-Za-z0-9_-]{20,}"),
    "AWS access key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "raw capability": re.compile(rb'["\']c_[A-Za-z0-9_-]{8,}["\']'),
}
MAX_SOURCE_BLOB_BYTES = 10 * 1024 * 1024


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, encoding="utf-8"
    )


def validate_schemas() -> int:
    try:
        from jsonschema.validators import validator_for
    except ImportError as exc:
        raise RuntimeError(
            "Install requirements-dev.txt before schema validation"
        ) from exc
    checked = 0
    paths = [
        *(ROOT / "schemas").glob("*.json"),
        *(ROOT / "quorune" / "schemas").glob("*.json"),
    ]
    for path in sorted(paths):
        schema = json.loads(path.read_text(encoding="utf-8"))
        validator_for(schema).check_schema(schema)
        checked += 1
    return checked


def validate_public_fixture() -> None:
    path = ROOT / "tests" / "fixtures" / "sanitized-replay-smoke.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    required = {"schema_version", "fixture_kind", "seed", "profile", "decks", "actions"}
    missing = sorted(required - set(fixture))
    if missing:
        raise ValueError(f"{path} is missing {missing}")
    serialized = path.read_bytes()
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(serialized):
            raise ValueError(f"{path} contains {label}")


def validate_tracked_files() -> tuple[int, int]:
    history_revision = _git("rev-parse", "--verify", "HEAD").strip()
    tracked = [line for line in _git("ls-files").splitlines() if line]
    errors: list[str] = []
    scanned_bytes = 0
    for relative in tracked:
        normalized = relative.replace("\\", "/")
        if any(pattern.search(normalized) for pattern in FORBIDDEN_TRACKED):
            errors.append(f"forbidden tracked artifact: {normalized}")
            continue
        path = ROOT / relative
        if not path.is_file():
            continue
        data = path.read_bytes()
        scanned_bytes += len(data)
        if len(data) > MAX_SOURCE_BLOB_BYTES:
            errors.append(f"tracked file exceeds 10 MiB: {normalized}")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(data):
                errors.append(f"{normalized} contains {label}")
    historical_paths = {
        line.replace("\\", "/")
        for line in _git(
            "log", history_revision, "--name-only", "--pretty=format:"
        ).splitlines()
        if line
    }
    for relative in sorted(historical_paths):
        if any(pattern.search(relative) for pattern in FORBIDDEN_TRACKED):
            errors.append(f"forbidden artifact remains in history: {relative}")
    objects = _git("rev-list", "--objects", history_revision).splitlines()
    if objects:
        batch = subprocess.run(
            [
                "git",
                "cat-file",
                "--batch-check=%(objecttype) %(objectname) %(objectsize)",
            ],
            cwd=ROOT,
            input="\n".join(line.split(" ", 1)[0] for line in objects) + "\n",
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            check=True,
        ).stdout.splitlines()
        paths_by_oid = {
            line.split(" ", 1)[0]: (
                line.split(" ", 1)[1] if " " in line else ""
            )
            for line in objects
        }
        for line in batch:
            kind, object_id, size = line.split(" ", 2)
            if kind == "blob" and int(size) > MAX_SOURCE_BLOB_BYTES:
                errors.append(
                    "historical blob exceeds 10 MiB: "
                    f"{paths_by_oid.get(object_id, object_id)}"
                )
    if errors:
        raise ValueError("\n".join(sorted(set(errors))))
    return len(tracked), scanned_bytes


def validate_generated_platform_status() -> None:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "update_platform_status.py"),
            "--check",
        ],
        cwd=ROOT,
        check=True,
    )


def validate_generated_architecture_audit() -> None:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "update_architecture_audit.py"),
            "--check",
        ],
        cwd=ROOT,
        check=True,
    )


def validate_generated_protocol_reference() -> None:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "update_protocol_reference.py"),
            "--check",
        ],
        cwd=ROOT,
        check=True,
    )


def validate_generated_rules_queue() -> None:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "update_rules_scheduler.py"),
            "--check",
        ],
        cwd=ROOT,
        check=True,
    )


def validate_architecture_policy() -> None:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "validate_architecture.py"),
            "--check",
        ],
        cwd=ROOT,
        check=True,
    )


def validate_documentation_policy() -> None:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "validate_documentation.py"),
            "--check",
        ],
        cwd=ROOT,
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    runtime = validate_python_runtime(ROOT)
    schemas = validate_schemas()
    validate_public_fixture()
    validate_generated_platform_status()
    validate_generated_architecture_audit()
    validate_generated_protocol_reference()
    validate_generated_rules_queue()
    validate_architecture_policy()
    validate_documentation_policy()
    tracked, scanned_bytes = validate_tracked_files()
    print(
        json.dumps(
            {
                "ok": True,
                "python_runtime": runtime,
                "schemas_checked": schemas,
                "tracked_files_checked": tracked,
                "tracked_bytes_scanned": scanned_bytes,
                "public_replay_fixture": "pass",
                "secret_and_capability_scan": "pass",
                "history_artifact_scan": "pass",
                "platform_status_stale_check": "pass",
                "architecture_audit_stale_check": "pass",
                "protocol_reference_stale_check": "pass",
                "rules_queue_stale_check": "pass",
                "architecture_policy": "pass",
                "documentation_policy": "pass",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"repository validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
