from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.generated_artifacts import (
    GeneratorSpec,
    all_outputs,
    check_command,
    load_input_groups,
    load_manifest,
    write_command,
)
from scripts.generated_finalization_receipt import write_finalization_receipt
from scripts.generated_owner_cache import (
    GeneratedOwnerCacheError,
    OwnerArtifactReceipt,
    affected_owner_plan,
    build_owner_receipt,
    compiler_identity_status,
    database_builder_input_fingerprint,
    owner_cache_directory,
    owner_input_identity,
    pinned_database_identity,
    read_owner_receipt,
    restore_owner_artifact,
    store_owner_artifact,
)
from scripts.source_tree_fingerprint import tracked_worktree_source_fingerprint
from scripts.validate_python_runtime import require_supported_python


SCHEMA_VERSION = 2
_LOCAL_ROOT = (ROOT / "local").resolve()
_DEFAULT_CACHE_ROOT = _LOCAL_ROOT / "generated-owner-cache"


class CloudGeneratedArtifactError(RuntimeError):
    """A cloud-generated owner or bundle is malformed or unsafe."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _git_blob_oid(path: Path, repository_path: str) -> str:
    """Hash a file through the repository's attributes and clean filters."""

    result = subprocess.run(
        ["git", "hash-object", f"--path={repository_path}", str(path)],
        cwd=ROOT,
        check=False,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    value = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise CloudGeneratedArtifactError(
            f"Could not compute Git-normalized content for {repository_path}"
        )
    return value


def _safe_stage_directory(raw: str | Path) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    resolved = path.resolve()
    try:
        resolved.relative_to(_LOCAL_ROOT)
    except ValueError as exc:
        raise CloudGeneratedArtifactError(
            "Cloud artifact staging directories must stay below local/"
        ) from exc
    if resolved == _LOCAL_ROOT:
        raise CloudGeneratedArtifactError(
            "Cloud artifact staging cannot replace the whole local directory"
        )
    return resolved


def _reset_stage_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def _run(label: str, command: Sequence[str]) -> None:
    print(f"[{label}] {' '.join(command)}", flush=True)
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode:
        raise CloudGeneratedArtifactError(
            f"{label} failed with exit code {result.returncode}"
        )


def _spec(specs: Sequence[GeneratorSpec], owner: str) -> GeneratorSpec:
    result = next((spec for spec in specs if spec.id == owner), None)
    if result is None:
        raise CloudGeneratedArtifactError(f"Unknown generated owner: {owner}")
    return result


def _copy_outputs(outputs: Iterable[str], stage: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in outputs:
        source = ROOT / relative
        if not source.is_file():
            raise CloudGeneratedArtifactError(
                f"Generated output is missing: {relative}"
            )
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        result[relative] = _sha256(destination)
    return result


def _source_commit() -> str:
    expected = os.environ.get("CLOUD_GENERATED_SOURCE_SHA") or os.environ.get(
        "GITHUB_SHA"
    )
    observed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
    ).stdout.strip()
    if expected and expected != observed:
        raise CloudGeneratedArtifactError(
            "GitHub source SHA does not match the checked-out commit"
        )
    return observed


def _snapshot_metadata() -> dict[str, Any]:
    rules = json.loads((ROOT / "rules" / "manifest.json").read_text(encoding="utf-8"))
    cards = rules.get("card_data_snapshot") or {}
    return {
        "rules_effective_date": rules.get("effective_date"),
        "rules_source_sha256": rules.get("source_sha256"),
        "oracle_source_sha256": (cards.get("oracle_bulk") or {}).get("sha256"),
        "rulings_source_sha256": (cards.get("rulings_bulk") or {}).get("sha256"),
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _cache_root(raw: str | None) -> Path:
    if raw is None:
        return _DEFAULT_CACHE_ROOT
    return _safe_stage_directory(raw)


def _owner_identity(
    selected: GeneratorSpec,
    specs: Sequence[GeneratorSpec],
    database: str | None,
):
    database_path = Path(database).resolve() if database else None
    return owner_input_identity(
        selected,
        specs=specs,
        input_groups=load_input_groups(),
        root=ROOT,
        database=database_path,
    )


def owner_key(
    owner: str,
    database: str | None,
    cache_root: str | None,
) -> dict[str, Any]:
    specs = load_manifest()
    selected = _spec(specs, owner)
    if selected.reuse_policy != "safe":
        raise CloudGeneratedArtifactError(
            f"Generated owner {owner} is explicitly noncacheable"
        )
    identity = _owner_identity(selected, specs, database)
    path = owner_cache_directory(_cache_root(cache_root), identity)
    result = {
        "owner": owner,
        "owner_fingerprint": identity.fingerprint,
        "cache_path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "database_fingerprint": identity.database_fingerprint,
    }
    _write_github_outputs(result)
    return result


def _write_github_outputs(values: Mapping[str, object]) -> None:
    destination = os.environ.get("GITHUB_OUTPUT")
    if not destination:
        return
    with Path(destination).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            if value is not None and isinstance(value, (str, int, bool)):
                handle.write(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n")


def run_owner(
    owner: str,
    stage_dir: str,
    database: str | None,
    cache_root: str | None = None,
    affected_owners_json: str | None = None,
    force_reason: str | None = None,
) -> dict[str, Any]:
    specs = load_manifest()
    selected = _spec(specs, owner)
    database_path = Path(database).resolve() if database else None
    command = write_command(
        selected,
        database=database_path,
        include_manual=False,
    )
    if command is None:
        raise CloudGeneratedArtifactError(
            f"Generated owner {owner} has no cloud-safe write command"
        )
    if force_reason is not None and selected.reuse_policy != "safe":
        raise CloudGeneratedArtifactError(
            f"Generated owner {owner} is noncacheable and cannot be force-recomputed"
        )
    cache_hit = False
    inherited = False
    artifact_receipt = None
    identity = None
    if selected.reuse_policy == "safe":
        identity = _owner_identity(selected, specs, database)
        artifact_dir = owner_cache_directory(_cache_root(cache_root), identity)
        if force_reason is not None and not force_reason.strip():
            raise CloudGeneratedArtifactError(
                "Forced owner recomputation requires a nonempty reason"
            )
        if artifact_dir.exists() and force_reason is None:
            artifact_receipt = restore_owner_artifact(
                selected,
                identity,
                artifact_dir=artifact_dir,
                root=ROOT,
            )
            cache_hit = True
        else:
            if affected_owners_json is not None:
                try:
                    affected = json.loads(affected_owners_json)
                except json.JSONDecodeError as exc:
                    raise CloudGeneratedArtifactError(
                        "Affected-owner plan is not valid JSON"
                    ) from exc
                if not isinstance(affected, list) or not all(
                    isinstance(item, str) for item in affected
                ):
                    raise CloudGeneratedArtifactError(
                        "Affected-owner plan must be a JSON string list"
                    )
                inherited = owner not in affected
            if force_reason is not None:
                inherited = False
            if not inherited:
                _run(owner, command)
                _run(f"check:{owner}", check_command(selected))
            else:
                try:
                    _run(f"check:{owner}", check_command(selected))
                except CloudGeneratedArtifactError:
                    print(
                        f"[{owner}] inherited outputs are stale; generating "
                        "the content-keyed owner",
                        flush=True,
                    )
                    inherited = False
                    _run(owner, command)
                    _run(f"check:{owner}", check_command(selected))
            if artifact_dir.exists():
                cached = read_owner_receipt(artifact_dir / "_owner_receipt.json")
                regenerated = build_owner_receipt(selected, identity, root=ROOT)
                if cached != regenerated:
                    raise CloudGeneratedArtifactError(
                        f"Forced owner recomputation contradicted immutable cache: {owner}"
                    )
                artifact_receipt = cached
            else:
                artifact_receipt = store_owner_artifact(
                    selected,
                    identity,
                    artifact_dir=artifact_dir,
                    root=ROOT,
                )
    else:
        _run(owner, command)
        _run(f"check:{owner}", check_command(selected))

    if cache_hit:
        _run(f"check:{owner}", check_command(selected))

    stage = _safe_stage_directory(stage_dir)
    _reset_stage_directory(stage)
    hashes = _copy_outputs(selected.outputs, stage)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "kind": "generated_owner",
        "owner": owner,
        "cache_hit": cache_hit,
        "execution": (
            "forced"
            if force_reason is not None
            else "cache"
            if cache_hit
            else "inherited"
            if inherited
            else "generated"
        ),
        "force_reason": force_reason,
        "source_commit": _source_commit(),
        "source_tree_fingerprint": tracked_worktree_source_fingerprint(ROOT),
        "outputs": hashes,
        "artifact_receipt": (
            artifact_receipt.to_dict() if artifact_receipt is not None else None
        ),
        **_snapshot_metadata(),
    }
    _write_json(stage / "_cloud" / "owners" / f"{owner}.json", receipt)
    return receipt


def plan(base_ref: str) -> dict[str, Any]:
    result = affected_owner_plan(base_ref=base_ref, root=ROOT)
    compact = {
        "affected_owners": json.dumps(result["owners"], separators=(",", ":")),
        "database_required": result["database_required"],
        "earliest_owner": result["earliest_owner"] or "",
    }
    _write_github_outputs(compact)
    return result


def database_key() -> dict[str, Any]:
    result = {
        "database_fingerprint": database_builder_input_fingerprint(root=ROOT),
    }
    _write_github_outputs(result)
    return result


def validate_database(database: str) -> dict[str, Any]:
    fingerprint = pinned_database_identity(Path(database), root=ROOT)
    result = {"database_fingerprint": fingerprint}
    _write_github_outputs(result)
    return result


def verify_compiler_identity(base_ref: str) -> dict[str, Any]:
    result = compiler_identity_status(base_ref=base_ref, root=ROOT)
    if not result["ok"]:
        raise CloudGeneratedArtifactError(
            "Semantic compiler inputs changed without a compiler or schema identity bump"
        )
    return result


def _owner_receipt_inventory(
    specs: Sequence[GeneratorSpec],
    *,
    required: bool,
) -> dict[str, str]:
    inventory: dict[str, str] = {}
    receipt_root = ROOT / "_cloud" / "owners"
    source_commit = _source_commit()
    for spec in specs:
        if spec.reuse_policy != "safe":
            continue
        path = receipt_root / f"{spec.id}.json"
        if not path.is_file():
            if required:
                raise CloudGeneratedArtifactError(
                    f"Assembled bundle is missing owner receipt: {spec.id}"
                )
            continue
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CloudGeneratedArtifactError(
                f"Assembled bundle owner receipt is malformed: {spec.id}"
            ) from exc
        if (
            not isinstance(envelope, Mapping)
            or envelope.get("schema_version") != SCHEMA_VERSION
            or envelope.get("kind") != "generated_owner"
            or envelope.get("owner") != spec.id
            or envelope.get("source_commit") != source_commit
        ):
            raise CloudGeneratedArtifactError(
                f"Assembled bundle owner receipt is stale: {spec.id}"
            )
        raw_artifact = envelope.get("artifact_receipt")
        if not isinstance(raw_artifact, Mapping):
            raise CloudGeneratedArtifactError(
                f"Assembled bundle has no reusable artifact receipt: {spec.id}"
            )
        artifact = OwnerArtifactReceipt.from_dict(raw_artifact)
        if artifact.owner != spec.id or {
            relative for relative, _raw, _blob in artifact.outputs
        } != set(spec.outputs):
            raise CloudGeneratedArtifactError(
                f"Assembled bundle owner receipt has the wrong outputs: {spec.id}"
            )
        for relative, raw_hash, blob_oid in artifact.outputs:
            output = ROOT / relative
            if (
                not output.is_file()
                or _sha256(output) != raw_hash
                or _git_blob_oid(output, relative) != blob_oid
            ):
                raise CloudGeneratedArtifactError(
                    f"Assembled bundle output contradicts its owner receipt: {relative}"
                )
        inventory[spec.id] = artifact.input_fingerprint
    return inventory


def stage_bundle(
    stage_dir: str,
    *,
    require_owner_receipts: bool = False,
) -> dict[str, Any]:
    specs = load_manifest()
    owner_fingerprints = _owner_receipt_inventory(
        specs,
        required=require_owner_receipts,
    )
    stage = _safe_stage_directory(stage_dir)
    _reset_stage_directory(stage)
    hashes = _copy_outputs(all_outputs(specs), stage)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "kind": "generated_bundle",
        "source_commit": _source_commit(),
        "source_tree_fingerprint": tracked_worktree_source_fingerprint(ROOT),
        "generator_count": len(specs),
        "output_count": len(hashes),
        "owner_input_fingerprints": owner_fingerprints,
        "outputs": hashes,
        **_snapshot_metadata(),
    }
    _write_json(stage / "_cloud" / "bundle.json", receipt)
    return receipt


def install_bundle(
    bundle_dir: str,
    expected_commit: str | None,
    *,
    write_receipt: bool = True,
) -> dict[str, Any]:
    bundle = Path(bundle_dir).resolve()
    receipt_path = bundle / "_cloud" / "bundle.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CloudGeneratedArtifactError(
            "Downloaded cloud bundle has no valid receipt"
        ) from exc
    specs = load_manifest()
    expected_outputs = set(all_outputs(specs))
    outputs = receipt.get("outputs")
    if (
        receipt.get("schema_version") != SCHEMA_VERSION
        or receipt.get("kind") != "generated_bundle"
        or not isinstance(outputs, dict)
        or set(outputs) != expected_outputs
    ):
        raise CloudGeneratedArtifactError(
            "Downloaded cloud bundle does not match the generated manifest"
        )
    if expected_commit and receipt.get("source_commit") != expected_commit:
        raise CloudGeneratedArtifactError(
            "Downloaded cloud bundle belongs to a different source commit"
        )
    current_commit = _source_commit()
    if receipt.get("source_commit") != current_commit:
        raise CloudGeneratedArtifactError(
            "Downloaded cloud bundle does not match the current HEAD"
        )

    changed: list[str] = []
    for relative in sorted(expected_outputs):
        source = bundle / relative
        if not source.is_file() or _sha256(source) != outputs[relative]:
            raise CloudGeneratedArtifactError(
                f"Downloaded cloud output is missing or corrupt: {relative}"
            )
        destination = ROOT / relative
        if not destination.is_file() or _git_blob_oid(
            destination, relative
        ) != _git_blob_oid(source, relative):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            changed.append(relative)
    source_fingerprint = tracked_worktree_source_fingerprint(ROOT)
    if source_fingerprint != receipt.get("source_tree_fingerprint"):
        raise CloudGeneratedArtifactError(
            "Downloaded cloud bundle does not match the local source tree"
        )
    receipt_path = None
    if write_receipt:
        receipt_path, _ = write_finalization_receipt(
            specs,
            database=None,
            root=ROOT,
        )
    return {
        "ok": True,
        "source_commit": receipt["source_commit"],
        "installed_outputs": len(expected_outputs),
        "changed_outputs": changed,
        "finalization_receipt": str(receipt_path) if receipt_path else None,
    }


def main() -> int:
    require_supported_python()
    parser = argparse.ArgumentParser(
        description="Run, stage, or install governed cloud-generated artifacts"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    owner = subparsers.add_parser("run-owner")
    owner.add_argument("--owner", required=True)
    owner.add_argument("--stage-dir", required=True)
    owner.add_argument("--db")
    owner.add_argument("--cache-root")
    owner.add_argument("--affected-owners-json")
    owner.add_argument("--force-reason")

    key = subparsers.add_parser("owner-key")
    key.add_argument("--owner", required=True)
    key.add_argument("--db")
    key.add_argument("--cache-root")

    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--base-ref", required=True)

    subparsers.add_parser("database-key")

    validate_database_parser = subparsers.add_parser("validate-database")
    validate_database_parser.add_argument("--db", required=True)

    compiler_identity = subparsers.add_parser("verify-compiler-identity")
    compiler_identity.add_argument("--base-ref", required=True)

    bundle = subparsers.add_parser("stage-bundle")
    bundle.add_argument("--stage-dir", required=True)
    bundle.add_argument("--require-owner-receipts", action="store_true")

    install = subparsers.add_parser("install-bundle")
    install.add_argument("--bundle-dir", required=True)
    install.add_argument("--expected-commit", required=True)

    args = parser.parse_args()
    try:
        if args.command == "run-owner":
            result = run_owner(
                args.owner,
                args.stage_dir,
                args.db,
                args.cache_root,
                args.affected_owners_json,
                args.force_reason,
            )
        elif args.command == "owner-key":
            result = owner_key(args.owner, args.db, args.cache_root)
        elif args.command == "plan":
            result = plan(args.base_ref)
        elif args.command == "database-key":
            result = database_key()
        elif args.command == "validate-database":
            result = validate_database(args.db)
        elif args.command == "verify-compiler-identity":
            result = verify_compiler_identity(args.base_ref)
        elif args.command == "stage-bundle":
            result = stage_bundle(
                args.stage_dir,
                require_owner_receipts=args.require_owner_receipts,
            )
        else:
            result = install_bundle(args.bundle_dir, args.expected_commit)
    except (
        CloudGeneratedArtifactError,
        GeneratedOwnerCacheError,
        OSError,
        ValueError,
    ) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
