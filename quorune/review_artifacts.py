from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
from typing import TYPE_CHECKING, Any, Mapping, Sequence
import weakref

from .util import stable_json

if TYPE_CHECKING:
    from .engine import CommanderEngine


_REVIEW_LOCKS: weakref.WeakValueDictionary[Path, threading.RLock] = (
    weakref.WeakValueDictionary()
)
_REVIEW_LOCKS_GUARD = threading.Lock()


def _review_lock(directory: Path) -> threading.RLock:
    key = directory.resolve()
    with _REVIEW_LOCKS_GUARD:
        return _REVIEW_LOCKS.setdefault(key, threading.RLock())


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def record_size_comparison(
    directory: str | Path,
    *,
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Measure one durable record without re-deriving its semantic review."""

    directory = Path(directory)
    migrated_from = (manifest or {}).get("migrated_from")
    before_bytes = (
        Path(str(migrated_from)).stat().st_size
        if migrated_from and Path(str(migrated_from)).exists()
        else None
    )
    component_bytes = {
        path.name: path.stat().st_size
        for path in directory.glob("*")
        if path.is_file()
    }
    core_names = {
        "manifest.json",
        "checkpoint.json",
        "initial-checkpoint.json.gz",
        "commands.jsonl",
        "events.jsonl",
        "decisions.jsonl",
        "semantics.json",
        "cursors.json",
        "pilot-profiles.json",
        "plans.json",
        "pilot-memory.json",
    }
    resumable_core = sum(
        value for name, value in component_bytes.items() if name in core_names
    )
    review_artifacts = sum(
        value
        for name, value in component_bytes.items()
        if name in {"review.json", "review.md"}
    )
    record_total = sum(component_bytes.values())
    return {
        "legacy_game_json_bytes": before_bytes,
        "record_components_bytes": component_bytes,
        "checkpoint_bytes": component_bytes.get("checkpoint.json", 0),
        "initial_checkpoint_bytes": component_bytes.get(
            "initial-checkpoint.json.gz", 0
        ),
        "command_journal_bytes": component_bytes.get("commands.jsonl", 0),
        "event_journal_bytes": component_bytes.get("events.jsonl", 0),
        "decision_journal_bytes": component_bytes.get("decisions.jsonl", 0),
        "manifest_bytes": component_bytes.get("manifest.json", 0),
        "review_artifact_bytes": review_artifacts,
        "resumable_core_bytes": resumable_core,
        "complete_record_bytes": record_total,
        "record_total_bytes": record_total,
        "bytes_saved_before_derived_review": (
            before_bytes - resumable_core
            if before_bytes is not None
            else None
        ),
        "percent_smaller_before_derived_review": (
            round((before_bytes - resumable_core) * 100 / before_bytes, 1)
            if before_bytes
            else None
        ),
    }


def _publish_review(
    directory: Path,
    engine: CommanderEngine,
    *,
    decisions: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any] | None,
) -> dict[str, Any]:
    # Keep publication independent from engine/runtime registry initialization.
    # The report stack is imported only when an artifact is actually derived,
    # so architecture audits remain deterministic under test discovery.
    from .report import derive_review, review_markdown

    review = derive_review(
        engine,
        decisions=decisions,
        manifest=manifest,
        record_directory=directory,
    )
    if manifest is not None:
        updated = dict(manifest)
        updated["review"] = {
            "classification": review["fidelity"]["classification"],
            "eligible": review["fidelity"]["review_eligible"],
            "matchup_evidence": review["fidelity"]["matchup_evidence"],
        }
        _atomic_text(directory / "manifest.json", stable_json(updated))
        manifest = updated
    # Size fields include the derived artifacts themselves. Iterate until their
    # decimal byte counts stabilize so review.json and review.md use the same
    # definitions and values.
    previous_sizes: Mapping[str, Any] | None = None
    for _ in range(5):
        _atomic_text(directory / "review.json", stable_json(review))
        _atomic_text(directory / "review.md", review_markdown(review))
        sizes = record_size_comparison(directory, manifest=manifest)
        review["size_comparison"] = sizes
        if sizes == previous_sizes:
            break
        previous_sizes = sizes
    _atomic_text(directory / "review.json", stable_json(review))
    _atomic_text(directory / "review.md", review_markdown(review))
    return review


def write_review_artifacts(
    directory: str | Path,
    engine: CommanderEngine,
    *,
    decisions: Sequence[Mapping[str, Any]] = (),
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    directory = Path(directory)
    # Windows cannot reliably replace a destination while another thread is
    # publishing the same artifact set. Serialize the complete derived bundle
    # while preserving per-file atomic replacement and the prior valid files.
    with _review_lock(directory):
        return _publish_review(
            directory,
            engine,
            decisions=decisions,
            manifest=manifest,
        )
