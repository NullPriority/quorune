from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping, Sequence

try:
    from scripts.generated_artifacts import GeneratorSpec, all_outputs
    from scripts.source_tree_fingerprint import (
        tracked_worktree_source_fingerprint,
    )
except ModuleNotFoundError:  # Direct execution from scripts/.
    from generated_artifacts import (  # type: ignore[no-redef]
        GeneratorSpec,
        all_outputs,
    )
    from source_tree_fingerprint import (  # type: ignore[no-redef]
        tracked_worktree_source_fingerprint,
    )


RECEIPT_SCHEMA_VERSION = 1
RECEIPT_FINGERPRINT_ALGORITHM = (
    "tracked-source-generated-outputs-database-stat-sha256-v1"
)
RECEIPT_GIT_PATH = "quorune/generated-finalization-receipt.json"
_SHA256 = re.compile(r"[0-9a-f]{64}")


class GeneratedFinalizationReceiptError(ValueError):
    """A local generated-finalization receipt is missing or stale."""


@dataclass(frozen=True)
class GeneratedFinalizationReceipt:
    schema_version: int
    fingerprint_algorithm: str
    source_tree_fingerprint: str
    generated_outputs_fingerprint: str
    database_path: str | None
    database_fingerprint: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "fingerprint_algorithm": self.fingerprint_algorithm,
            "source_tree_fingerprint": self.source_tree_fingerprint,
            "generated_outputs_fingerprint": (
                self.generated_outputs_fingerprint
            ),
            "database_path": self.database_path,
            "database_fingerprint": self.database_fingerprint,
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> GeneratedFinalizationReceipt:
        expected = {
            "schema_version",
            "fingerprint_algorithm",
            "source_tree_fingerprint",
            "generated_outputs_fingerprint",
            "database_path",
            "database_fingerprint",
        }
        if set(value) != expected:
            raise GeneratedFinalizationReceiptError(
                "generated-finalization receipt fields are incomplete or unknown"
            )
        schema_version = value.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or schema_version != RECEIPT_SCHEMA_VERSION
        ):
            raise GeneratedFinalizationReceiptError(
                "generated-finalization receipt schema is unsupported"
            )
        if (
            value.get("fingerprint_algorithm")
            != RECEIPT_FINGERPRINT_ALGORITHM
        ):
            raise GeneratedFinalizationReceiptError(
                "generated-finalization receipt algorithm is unsupported"
            )
        for field in (
            "source_tree_fingerprint",
            "generated_outputs_fingerprint",
        ):
            fingerprint = value.get(field)
            if not isinstance(fingerprint, str) or not _SHA256.fullmatch(
                fingerprint
            ):
                raise GeneratedFinalizationReceiptError(
                    f"generated-finalization receipt {field} is invalid"
                )
        database_path = value.get("database_path")
        database_fingerprint = value.get("database_fingerprint")
        if (database_path is None) != (database_fingerprint is None):
            raise GeneratedFinalizationReceiptError(
                "generated-finalization receipt database identity is incomplete"
            )
        if database_path is not None and (
            not isinstance(database_path, str) or not database_path
        ):
            raise GeneratedFinalizationReceiptError(
                "generated-finalization receipt database_path is invalid"
            )
        if database_fingerprint is not None and (
            not isinstance(database_fingerprint, str)
            or not _SHA256.fullmatch(database_fingerprint)
        ):
            raise GeneratedFinalizationReceiptError(
                "generated-finalization receipt database_fingerprint is invalid"
            )
        return cls(
            schema_version=RECEIPT_SCHEMA_VERSION,
            fingerprint_algorithm=RECEIPT_FINGERPRINT_ALGORITHM,
            source_tree_fingerprint=str(
                value["source_tree_fingerprint"]
            ),
            generated_outputs_fingerprint=str(
                value["generated_outputs_fingerprint"]
            ),
            database_path=(
                str(database_path) if database_path is not None else None
            ),
            database_fingerprint=(
                str(database_fingerprint)
                if database_fingerprint is not None
                else None
            ),
        )


def _fingerprint_entries(entries: Sequence[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    digest.update((RECEIPT_FINGERPRINT_ALGORITHM + "\0").encode("ascii"))
    for name, value in entries:
        name_bytes = name.encode("utf-8")
        value_bytes = value.encode("utf-8")
        digest.update(len(name_bytes).to_bytes(8, "big"))
        digest.update(name_bytes)
        digest.update(len(value_bytes).to_bytes(8, "big"))
        digest.update(value_bytes)
    return digest.hexdigest()


def generated_outputs_fingerprint(
    specs: Sequence[GeneratorSpec],
    *,
    root: Path,
) -> str:
    entries: list[tuple[str, str]] = []
    for relative in sorted(all_outputs(specs)):
        path = root / relative
        if not path.is_file():
            raise GeneratedFinalizationReceiptError(
                f"generated output is missing: {relative}"
            )
        try:
            fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise GeneratedFinalizationReceiptError(
                f"generated output is unreadable: {relative}"
            ) from exc
        entries.append((relative, fingerprint))
    return _fingerprint_entries(entries)


def database_identity(
    database: Path | None,
) -> tuple[str | None, str | None]:
    if database is None:
        return None, None
    try:
        resolved = database.resolve(strict=True)
        resolved.stat()
    except OSError as exc:
        raise GeneratedFinalizationReceiptError(
            f"finalization database is unavailable: {database}"
        ) from exc
    path = str(resolved)
    entries: list[tuple[str, str]] = [("path", path)]
    for label, candidate in (
        ("database", resolved),
        ("wal", Path(path + "-wal")),
        ("shm", Path(path + "-shm")),
    ):
        if not candidate.exists():
            entries.append((f"{label}.present", "false"))
            continue
        try:
            stat = candidate.stat()
        except OSError as exc:
            raise GeneratedFinalizationReceiptError(
                f"finalization database companion is unavailable: {candidate}"
            ) from exc
        entries.extend(
            (
                (f"{label}.present", "true"),
                (f"{label}.size", str(stat.st_size)),
                (f"{label}.mtime_ns", str(stat.st_mtime_ns)),
            )
        )
    fingerprint = _fingerprint_entries(entries)
    return path, fingerprint


def build_finalization_receipt(
    specs: Sequence[GeneratorSpec],
    *,
    database: Path | None,
    root: Path,
) -> GeneratedFinalizationReceipt:
    database_path, database_fingerprint = database_identity(database)
    try:
        source_fingerprint = tracked_worktree_source_fingerprint(root)
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        raise GeneratedFinalizationReceiptError(
            "unable to fingerprint the tracked worktree"
        ) from exc
    return GeneratedFinalizationReceipt(
        schema_version=RECEIPT_SCHEMA_VERSION,
        fingerprint_algorithm=RECEIPT_FINGERPRINT_ALGORITHM,
        source_tree_fingerprint=source_fingerprint,
        generated_outputs_fingerprint=generated_outputs_fingerprint(
            specs,
            root=root,
        ),
        database_path=database_path,
        database_fingerprint=database_fingerprint,
    )


def finalization_receipt_path(root: Path) -> Path:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--git-path", RECEIPT_GIT_PATH],
            cwd=root,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise GeneratedFinalizationReceiptError(
            "unable to locate Git metadata"
        ) from exc
    if completed.returncode:
        raise GeneratedFinalizationReceiptError(
            completed.stderr.strip() or "unable to locate Git metadata"
        )
    raw = Path(completed.stdout.strip())
    return raw if raw.is_absolute() else (root / raw).resolve()


def write_finalization_receipt(
    specs: Sequence[GeneratorSpec],
    *,
    database: Path | None,
    root: Path,
    expected_database_identity: tuple[str | None, str | None] | None = None,
) -> tuple[Path, GeneratedFinalizationReceipt]:
    receipt = build_finalization_receipt(
        specs,
        database=database,
        root=root,
    )
    observed_database_identity = (
        receipt.database_path,
        receipt.database_fingerprint,
    )
    if (
        expected_database_identity is not None
        and observed_database_identity != expected_database_identity
    ):
        raise GeneratedFinalizationReceiptError(
            "finalization database changed while generators were running"
        )
    destination = finalization_receipt_path(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(receipt.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination, receipt


def verify_finalization_receipt(
    specs: Sequence[GeneratorSpec],
    *,
    database: Path | None,
    root: Path,
) -> tuple[Path, GeneratedFinalizationReceipt]:
    path = finalization_receipt_path(root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GeneratedFinalizationReceiptError(
            "generated-finalization receipt is missing or malformed"
        ) from exc
    if not isinstance(value, Mapping):
        raise GeneratedFinalizationReceiptError(
            "generated-finalization receipt must contain a JSON object"
        )
    observed = GeneratedFinalizationReceipt.from_dict(value)
    verification_database = database
    if verification_database is None and observed.database_path is not None:
        verification_database = Path(observed.database_path)
    expected = build_finalization_receipt(
        specs,
        database=verification_database,
        root=root,
    )
    for field in expected.to_dict():
        if getattr(observed, field) != getattr(expected, field):
            raise GeneratedFinalizationReceiptError(
                "generated-finalization receipt is stale: " + field
            )
    return path, observed


__all__ = [
    "GeneratedFinalizationReceipt",
    "GeneratedFinalizationReceiptError",
    "RECEIPT_FINGERPRINT_ALGORITHM",
    "RECEIPT_GIT_PATH",
    "RECEIPT_SCHEMA_VERSION",
    "build_finalization_receipt",
    "database_identity",
    "finalization_receipt_path",
    "generated_outputs_fingerprint",
    "verify_finalization_receipt",
    "write_finalization_receipt",
]
