from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
from typing import Any, Callable, Mapping, Sequence

from scripts.generated_artifacts import (
    GeneratedArtifactInputGroups,
    GeneratorSpec,
    all_outputs,
    generator_manifest_fingerprint,
    load_input_groups,
    load_manifest,
    parse_input_groups,
    parse_manifest,
    topological_order,
)
from scripts.source_tree_fingerprint import canonical_tracked_blob_oids


OWNER_INPUT_ALGORITHM = "generated-owner-input-closure-sha256-v1"
OWNER_OUTPUT_ALGORITHM = "generated-owner-output-git-clean-sha256-v1"
OWNER_RECEIPT_SCHEMA_VERSION = 1
DATABASE_IDENTITY_ALGORITHM = "pinned-card-database-provenance-sha256-v1"
MANIFEST_GIT_PATH = "platform/generated-artifacts.json"
_IDENTITY_IMPLEMENTATION_PATHS = frozenset(
    {
        "scripts/generated_artifacts.py",
        "scripts/generated_owner_cache.py",
        "scripts/source_tree_fingerprint.py",
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_GIT_OID = re.compile(r"[0-9a-f]{40,64}")


class GeneratedOwnerCacheError(RuntimeError):
    """An owner input closure, receipt, or reusable artifact is invalid."""


@dataclass(frozen=True)
class OwnerInputIdentity:
    owner: str
    fingerprint: str
    manifest_fingerprint: str
    source_entries: tuple[tuple[str, str], ...]
    dependency_output_fingerprints: tuple[tuple[str, str], ...]
    database_fingerprint: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "algorithm": OWNER_INPUT_ALGORITHM,
            "owner": self.owner,
            "fingerprint": self.fingerprint,
            "manifest_fingerprint": self.manifest_fingerprint,
            "source_entries": dict(self.source_entries),
            "dependency_output_fingerprints": dict(
                self.dependency_output_fingerprints
            ),
            "database_fingerprint": self.database_fingerprint,
        }


@dataclass(frozen=True)
class OwnerArtifactReceipt:
    owner: str
    input_fingerprint: str
    manifest_fingerprint: str
    database_fingerprint: str | None
    dependency_output_fingerprints: tuple[tuple[str, str], ...]
    outputs: tuple[tuple[str, str, str], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": OWNER_RECEIPT_SCHEMA_VERSION,
            "kind": "generated_owner_artifact",
            "input_algorithm": OWNER_INPUT_ALGORITHM,
            "output_algorithm": OWNER_OUTPUT_ALGORITHM,
            "owner": self.owner,
            "input_fingerprint": self.input_fingerprint,
            "manifest_fingerprint": self.manifest_fingerprint,
            "database_fingerprint": self.database_fingerprint,
            "dependency_output_fingerprints": dict(
                self.dependency_output_fingerprints
            ),
            "outputs": {
                path: {"raw_sha256": raw, "git_blob_oid": blob}
                for path, raw, blob in self.outputs
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> OwnerArtifactReceipt:
        expected = {
            "schema_version",
            "kind",
            "input_algorithm",
            "output_algorithm",
            "owner",
            "input_fingerprint",
            "manifest_fingerprint",
            "database_fingerprint",
            "dependency_output_fingerprints",
            "outputs",
        }
        if set(value) != expected:
            raise GeneratedOwnerCacheError(
                "generated owner receipt fields are incomplete or unknown"
            )
        if (
            value.get("schema_version") != OWNER_RECEIPT_SCHEMA_VERSION
            or value.get("kind") != "generated_owner_artifact"
            or value.get("input_algorithm") != OWNER_INPUT_ALGORITHM
            or value.get("output_algorithm") != OWNER_OUTPUT_ALGORITHM
        ):
            raise GeneratedOwnerCacheError(
                "generated owner receipt schema or algorithm is unsupported"
            )
        owner = value.get("owner")
        if not isinstance(owner, str) or not owner:
            raise GeneratedOwnerCacheError("generated owner receipt owner is invalid")
        for field in ("input_fingerprint", "manifest_fingerprint"):
            candidate = value.get(field)
            if not isinstance(candidate, str) or not _SHA256.fullmatch(candidate):
                raise GeneratedOwnerCacheError(
                    f"generated owner receipt {field} is invalid"
                )
        database = value.get("database_fingerprint")
        if database is not None and (
            not isinstance(database, str) or not _SHA256.fullmatch(database)
        ):
            raise GeneratedOwnerCacheError(
                "generated owner receipt database fingerprint is invalid"
            )
        dependencies = _fingerprint_mapping(
            value.get("dependency_output_fingerprints"),
            field="dependency output fingerprints",
        )
        raw_outputs = value.get("outputs")
        if not isinstance(raw_outputs, Mapping) or not raw_outputs:
            raise GeneratedOwnerCacheError(
                "generated owner receipt outputs are invalid"
            )
        outputs: list[tuple[str, str, str]] = []
        for raw_path, raw_identity in raw_outputs.items():
            if (
                not isinstance(raw_path, str)
                or not raw_path
                or not isinstance(raw_identity, Mapping)
                or set(raw_identity) != {"raw_sha256", "git_blob_oid"}
            ):
                raise GeneratedOwnerCacheError(
                    "generated owner receipt output row is invalid"
                )
            raw_hash = raw_identity.get("raw_sha256")
            blob_oid = raw_identity.get("git_blob_oid")
            if (
                not isinstance(raw_hash, str)
                or not _SHA256.fullmatch(raw_hash)
                or not isinstance(blob_oid, str)
                or not _GIT_OID.fullmatch(blob_oid)
            ):
                raise GeneratedOwnerCacheError(
                    f"generated owner receipt output identity is invalid: {raw_path}"
                )
            outputs.append((raw_path, raw_hash, blob_oid))
        return cls(
            owner=owner,
            input_fingerprint=str(value["input_fingerprint"]),
            manifest_fingerprint=str(value["manifest_fingerprint"]),
            database_fingerprint=(str(database) if database is not None else None),
            dependency_output_fingerprints=dependencies,
            outputs=tuple(sorted(outputs)),
        )


def _fingerprint_mapping(
    value: object,
    *,
    field: str,
) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping):
        raise GeneratedOwnerCacheError(f"generated owner receipt {field} is invalid")
    rows: list[tuple[str, str]] = []
    for key, fingerprint in value.items():
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(fingerprint, str)
            or not _SHA256.fullmatch(fingerprint)
        ):
            raise GeneratedOwnerCacheError(
                f"generated owner receipt {field} is invalid"
            )
        rows.append((key, fingerprint))
    return tuple(sorted(rows))


def _canonical_json_fingerprint(algorithm: str, value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update((algorithm + "\0").encode("ascii"))
    digest.update(encoded)
    return digest.hexdigest()


def _run_git(
    root: Path,
    *args: str,
    input_bytes: bytes | None = None,
) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GeneratedOwnerCacheError(
            "unable to inspect the repository for generated owner identity"
        ) from exc
    return completed.stdout


def _glob_regex(pattern: str) -> re.Pattern[str]:
    result = ""
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                index += 2
                if index < len(pattern) and pattern[index] == "/":
                    result += "(?:.*/)?"
                    index += 1
                else:
                    result += ".*"
                continue
            result += "[^/]*"
        elif character == "?":
            result += "[^/]"
        else:
            result += re.escape(character)
        index += 1
    return re.compile("^" + result + "$")


def _match_patterns(paths: Sequence[str], patterns: Sequence[str]) -> set[str]:
    matchers = tuple(_glob_regex(pattern) for pattern in patterns)
    return {
        path
        for path in paths
        if any(matcher.fullmatch(path) for matcher in matchers)
    }


def _worktree_paths(root: Path) -> tuple[str, ...]:
    raw = _run_git(
        root,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
    )
    return tuple(
        sorted(
            path
            for path in (
                item.decode("utf-8", errors="strict")
                for item in raw.split(b"\0")
                if item
            )
            if (root / path).is_file()
        )
    )


def _ref_entries(root: Path, ref: str) -> dict[str, str]:
    raw = _run_git(root, "ls-tree", "-r", "-z", "--full-tree", ref)
    result: dict[str, str] = {}
    for row in raw.split(b"\0"):
        if not row:
            continue
        metadata, raw_path = row.split(b"\t", 1)
        _mode, kind, raw_oid = metadata.split(b" ", 2)
        if kind == b"blob":
            result[raw_path.decode("utf-8", errors="strict")] = raw_oid.decode(
                "ascii", errors="strict"
            )
    return result


def _ref_text_cache(
    root: Path,
    ref: str,
    paths: Sequence[str],
) -> dict[str, str]:
    if not paths:
        return {}
    requests = tuple(f"{ref}:{path}" for path in paths)
    raw = _run_git(
        root,
        "cat-file",
        "--batch",
        input_bytes=("\n".join(requests) + "\n").encode("utf-8"),
    )
    cursor = 0
    result: dict[str, str] = {}
    for relative, _request in zip(paths, requests, strict=True):
        header_end = raw.find(b"\n", cursor)
        if header_end < 0:
            raise GeneratedOwnerCacheError(
                "Git returned an incomplete generated-owner source batch"
            )
        header = raw[cursor:header_end].decode("ascii", errors="strict")
        fields = header.split(" ")
        if (
            len(fields) != 3
            or not _GIT_OID.fullmatch(fields[0])
            or fields[1] != "blob"
        ):
            raise GeneratedOwnerCacheError(
                f"Git returned an invalid generated-owner source row: {relative}"
            )
        try:
            size = int(fields[2])
        except ValueError as exc:
            raise GeneratedOwnerCacheError(
                f"Git returned an invalid generated-owner source size: {relative}"
            ) from exc
        content_start = header_end + 1
        content_end = content_start + size
        if content_end >= len(raw) or raw[content_end : content_end + 1] != b"\n":
            raise GeneratedOwnerCacheError(
                "Git returned an incomplete generated-owner source body"
            )
        result[relative] = raw[content_start:content_end].decode(
            "utf-8", errors="strict"
        )
        cursor = content_end + 1
    if cursor != len(raw):
        raise GeneratedOwnerCacheError(
            "Git returned unexpected generated-owner source batch data"
        )
    return result


def _module_candidates(
    relative: str,
    node: ast.Import | ast.ImportFrom,
) -> tuple[str, ...]:
    modules: list[str] = []
    if isinstance(node, ast.Import):
        modules.extend(alias.name for alias in node.names)
    else:
        current = list(PurePosixPath(relative).with_suffix("").parts[:-1])
        if node.level:
            keep = max(0, len(current) - node.level + 1)
            prefix = current[:keep]
        else:
            prefix = []
        if node.module:
            base = [*prefix, *node.module.split(".")]
        else:
            base = prefix
        if base:
            modules.append(".".join(base))
        modules.extend(
            ".".join([*base, alias.name])
            for alias in node.names
            if alias.name != "*"
        )
    candidates: set[str] = set()
    for module in modules:
        parts = module.split(".")
        if not parts or parts[0] not in {"quorune", "scripts", "server"}:
            continue
        stem = "/".join(parts)
        candidates.add(stem + ".py")
        candidates.add(stem + "/__init__.py")
    return tuple(sorted(candidates))


def _python_import_closure(
    initial: set[str],
    *,
    available: set[str],
    read_text: Callable[[str], str],
    import_cache: dict[str, tuple[str, ...]] | None = None,
    traverse_package_initializers: bool = True,
) -> set[str]:
    cached_imports = import_cache if import_cache is not None else {}
    closure = set(initial)
    pending = sorted(path for path in closure if path.endswith(".py"))
    parsed: set[str] = set()
    while pending:
        relative = pending.pop()
        if relative in parsed:
            continue
        parsed.add(relative)
        if (
            not traverse_package_initializers
            and PurePosixPath(relative).name == "__init__.py"
        ):
            continue
        candidates = cached_imports.get(relative)
        if candidates is None:
            try:
                tree = ast.parse(read_text(relative), filename=relative)
            except (OSError, UnicodeDecodeError, SyntaxError) as exc:
                raise GeneratedOwnerCacheError(
                    f"unable to parse generated owner implementation input: {relative}"
                ) from exc
            candidates = tuple(
                sorted(
                    {
                        candidate
                        for node in ast.walk(tree)
                        if isinstance(node, (ast.Import, ast.ImportFrom))
                        for candidate in _module_candidates(relative, node)
                    }
                )
            )
            cached_imports[relative] = candidates
        for candidate in candidates:
            if candidate in available and candidate not in closure:
                closure.add(candidate)
                pending.append(candidate)
    return closure


def _owner_patterns(
    spec: GeneratorSpec,
    input_groups: GeneratedArtifactInputGroups,
) -> tuple[str, ...]:
    grouped = [
        pattern
        for group_id in spec.input_groups
        for pattern in input_groups.patterns(group_id)
    ]
    return tuple([*grouped, *spec.input_paths])


def resolved_worktree_inputs(
    spec: GeneratorSpec,
    *,
    specs: Sequence[GeneratorSpec],
    input_groups: GeneratedArtifactInputGroups,
    root: Path,
    available_paths: Sequence[str] | None = None,
    import_cache: dict[str, tuple[str, ...]] | None = None,
) -> tuple[str, ...]:
    paths = (
        tuple(available_paths)
        if available_paths is not None
        else _worktree_paths(root)
    )
    available = set(paths)
    generated = set(all_outputs(specs))
    direct = _match_patterns(paths, _owner_patterns(spec, input_groups))
    direct -= generated | {MANIFEST_GIT_PATH}
    implementation = _match_patterns(paths, spec.implementation_inputs)
    implementation = _python_import_closure(
        implementation,
        available=available,
        read_text=lambda relative: (root / relative).read_text(encoding="utf-8"),
        import_cache=import_cache,
    )
    return tuple(
        sorted(direct | implementation | (available & _IDENTITY_IMPLEMENTATION_PATHS))
    )


def _path_entries(root: Path, paths: Sequence[str]) -> tuple[tuple[str, str], ...]:
    try:
        identities = canonical_tracked_blob_oids(root, list(paths))
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise GeneratedOwnerCacheError(
            "unable to compute Git-clean generated owner inputs"
        ) from exc
    return tuple(zip(paths, identities, strict=True))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise GeneratedOwnerCacheError(f"unable to hash artifact: {path}") from exc
    return digest.hexdigest()


def _output_rows(
    outputs: Sequence[str],
    *,
    root: Path,
) -> tuple[tuple[str, str, str], ...]:
    missing = [relative for relative in outputs if not (root / relative).is_file()]
    if missing:
        raise GeneratedOwnerCacheError(
            "generated owner outputs are missing: " + ", ".join(sorted(missing))
        )
    entries = _path_entries(root, sorted(outputs))
    return tuple(
        (relative, _sha256_file(root / relative), blob_oid)
        for relative, blob_oid in entries
    )


def owner_output_fingerprint(spec: GeneratorSpec, *, root: Path) -> str:
    rows = _output_rows(spec.outputs, root=root)
    return _canonical_json_fingerprint(
        OWNER_OUTPUT_ALGORITHM,
        [{"path": path, "git_blob_oid": blob} for path, _raw, blob in rows],
    )


def database_builder_input_fingerprint(*, root: Path) -> str:
    paths = (
        "quorune/bulk.py",
        "rules/manifest.json",
        "scripts/bootstrap_data.py",
        "scripts/generated_owner_cache.py",
    )
    for relative in paths:
        if not (root / relative).is_file():
            raise GeneratedOwnerCacheError(
                f"pinned database builder input is missing: {relative}"
            )
    return _canonical_json_fingerprint(
        DATABASE_IDENTITY_ALGORITHM,
        dict(_path_entries(root, paths)),
    )


def pinned_database_identity(database: Path, *, root: Path) -> str:
    try:
        manifest = json.loads(
            (root / "rules" / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise GeneratedOwnerCacheError("pinned rules manifest is unavailable") from exc
    snapshot = manifest.get("card_data_snapshot")
    if not isinstance(snapshot, Mapping):
        raise GeneratedOwnerCacheError(
            "pinned rules manifest has no card-data snapshot"
        )
    try:
        connection = sqlite3.connect(
            f"file:{database.resolve(strict=True).as_posix()}?mode=ro",
            uri=True,
        )
        connection.execute("PRAGMA query_only=ON")
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        if quick_check != ("ok",):
            raise GeneratedOwnerCacheError("pinned card database failed quick_check")
        schema_rows = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        table_names = [
            str(row[1]) for row in schema_rows if row[0] == "table"
        ]
        counts = {
            name: int(
                connection.execute(
                    'SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"'
                ).fetchone()[0]
            )
            for name in table_names
        }
    except (OSError, sqlite3.Error) as exc:
        raise GeneratedOwnerCacheError(
            "pinned card database is unavailable or invalid"
        ) from exc
    finally:
        if "connection" in locals():
            connection.close()
    return _canonical_json_fingerprint(
        DATABASE_IDENTITY_ALGORITHM,
        {
            "builder_inputs": database_builder_input_fingerprint(root=root),
            "snapshot": snapshot,
            "schema": schema_rows,
            "row_counts": counts,
        },
    )


def owner_input_identity(
    spec: GeneratorSpec,
    *,
    specs: Sequence[GeneratorSpec],
    input_groups: GeneratedArtifactInputGroups,
    root: Path,
    database: Path | None,
) -> OwnerInputIdentity:
    if spec.database_identity == "pinned-card-database":
        if database is None:
            raise GeneratedOwnerCacheError(
                f"generated owner {spec.id} requires the pinned card database"
            )
        database_fingerprint = pinned_database_identity(database, root=root)
    else:
        if database is not None:
            raise GeneratedOwnerCacheError(
                f"generated owner {spec.id} does not declare a database input"
            )
        database_fingerprint = None
    dependency_by_id = {candidate.id: candidate for candidate in specs}
    dependency_fingerprints = tuple(
        (
            dependency,
            owner_output_fingerprint(dependency_by_id[dependency], root=root),
        )
        for dependency in sorted(spec.depends_on)
    )
    source_paths = resolved_worktree_inputs(
        spec,
        specs=specs,
        input_groups=input_groups,
        root=root,
    )
    source_entries = _path_entries(root, source_paths)
    manifest_fingerprint = generator_manifest_fingerprint(spec)
    payload = {
        "owner": spec.id,
        "manifest_fingerprint": manifest_fingerprint,
        "source_entries": dict(source_entries),
        "dependency_output_fingerprints": dict(dependency_fingerprints),
        "database_fingerprint": database_fingerprint,
    }
    return OwnerInputIdentity(
        owner=spec.id,
        fingerprint=_canonical_json_fingerprint(OWNER_INPUT_ALGORITHM, payload),
        manifest_fingerprint=manifest_fingerprint,
        source_entries=source_entries,
        dependency_output_fingerprints=dependency_fingerprints,
        database_fingerprint=database_fingerprint,
    )


def build_owner_receipt(
    spec: GeneratorSpec,
    identity: OwnerInputIdentity,
    *,
    root: Path,
) -> OwnerArtifactReceipt:
    if spec.id != identity.owner:
        raise GeneratedOwnerCacheError("generated owner identity belongs to another owner")
    return OwnerArtifactReceipt(
        owner=spec.id,
        input_fingerprint=identity.fingerprint,
        manifest_fingerprint=identity.manifest_fingerprint,
        database_fingerprint=identity.database_fingerprint,
        dependency_output_fingerprints=identity.dependency_output_fingerprints,
        outputs=_output_rows(spec.outputs, root=root),
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_owner_receipt(
    destination: Path,
    receipt: OwnerArtifactReceipt,
) -> None:
    _write_json(destination, receipt.to_dict())


def read_owner_receipt(path: Path) -> OwnerArtifactReceipt:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GeneratedOwnerCacheError(
            "generated owner receipt is missing or malformed"
        ) from exc
    if not isinstance(value, Mapping):
        raise GeneratedOwnerCacheError(
            "generated owner receipt must contain a JSON object"
        )
    return OwnerArtifactReceipt.from_dict(value)


def _validate_receipt(
    spec: GeneratorSpec,
    identity: OwnerInputIdentity,
    receipt: OwnerArtifactReceipt,
) -> None:
    if (
        receipt.owner != spec.id
        or receipt.input_fingerprint != identity.fingerprint
        or receipt.manifest_fingerprint != identity.manifest_fingerprint
        or receipt.database_fingerprint != identity.database_fingerprint
        or receipt.dependency_output_fingerprints
        != identity.dependency_output_fingerprints
        or {path for path, _raw, _blob in receipt.outputs} != set(spec.outputs)
    ):
        raise GeneratedOwnerCacheError(
            f"generated owner cache receipt is stale: {spec.id}"
        )


def store_owner_artifact(
    spec: GeneratorSpec,
    identity: OwnerInputIdentity,
    *,
    artifact_dir: Path,
    root: Path,
) -> OwnerArtifactReceipt:
    if artifact_dir.exists() and any(artifact_dir.iterdir()):
        raise GeneratedOwnerCacheError(
            f"generated owner cache key already contains data: {spec.id}"
        )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    receipt = build_owner_receipt(spec, identity, root=root)
    for relative, _raw, _blob in receipt.outputs:
        destination = artifact_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, destination)
    write_owner_receipt(artifact_dir / "_owner_receipt.json", receipt)
    return receipt


def restore_owner_artifact(
    spec: GeneratorSpec,
    identity: OwnerInputIdentity,
    *,
    artifact_dir: Path,
    root: Path,
) -> OwnerArtifactReceipt:
    receipt = read_owner_receipt(artifact_dir / "_owner_receipt.json")
    _validate_receipt(spec, identity, receipt)
    for relative, raw_hash, _blob_oid in receipt.outputs:
        source = artifact_dir / relative
        if not source.is_file() or _sha256_file(source) != raw_hash:
            raise GeneratedOwnerCacheError(
                f"generated owner cache output is missing or corrupt: {relative}"
            )
    for relative, _raw_hash, _blob_oid in receipt.outputs:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(artifact_dir / relative, destination)
    observed = _output_rows(spec.outputs, root=root)
    if observed != receipt.outputs:
        raise GeneratedOwnerCacheError(
            f"generated owner cache output normalization changed: {spec.id}"
        )
    return receipt


def owner_cache_directory(
    cache_root: Path,
    identity: OwnerInputIdentity,
) -> Path:
    return cache_root / identity.owner / identity.fingerprint


def _manifest_at_ref(
    root: Path,
    ref: str,
) -> tuple[tuple[GeneratorSpec, ...], GeneratedArtifactInputGroups]:
    try:
        raw = _run_git(root, "show", f"{ref}:{MANIFEST_GIT_PATH}")
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GeneratedOwnerCacheError(
            "base generated-artifact manifest is unavailable"
        ) from exc
    if not isinstance(value, Mapping):
        raise GeneratedOwnerCacheError(
            "base generated-artifact manifest is invalid"
        )
    raw_groups = value.get("input_groups")
    if not isinstance(raw_groups, Mapping):
        raise GeneratedOwnerCacheError(
            "base generated-artifact manifest predates input closure declarations"
        )
    return parse_manifest(value), parse_input_groups(raw_groups)


def _resolved_ref_inputs(
    spec: GeneratorSpec,
    *,
    specs: Sequence[GeneratorSpec],
    input_groups: GeneratedArtifactInputGroups,
    root: Path,
    ref: str,
    entries: Mapping[str, str],
    content_cache: dict[str, str] | None = None,
    import_cache: dict[str, tuple[str, ...]] | None = None,
) -> tuple[str, ...]:
    paths = tuple(sorted(entries))
    available = set(paths)
    generated = set(all_outputs(specs))
    direct = _match_patterns(paths, _owner_patterns(spec, input_groups))
    direct -= generated | {MANIFEST_GIT_PATH}
    implementation = _match_patterns(paths, spec.implementation_inputs)
    cached_content = content_cache if content_cache is not None else {}

    def read_text(relative: str) -> str:
        content = cached_content.get(relative)
        if content is None:
            content = _run_git(root, "show", f"{ref}:{relative}").decode(
                "utf-8", errors="strict"
            )
            cached_content[relative] = content
        return content

    implementation = _python_import_closure(
        implementation,
        available=available,
        read_text=read_text,
        import_cache=import_cache,
    )
    return tuple(
        sorted(direct | implementation | (available & _IDENTITY_IMPLEMENTATION_PATHS))
    )


def _direct_identity_at_ref(
    spec: GeneratorSpec,
    *,
    specs: Sequence[GeneratorSpec],
    input_groups: GeneratedArtifactInputGroups,
    root: Path,
    ref: str,
    entries: Mapping[str, str],
    content_cache: dict[str, str] | None = None,
    import_cache: dict[str, tuple[str, ...]] | None = None,
) -> str:
    paths = _resolved_ref_inputs(
        spec,
        specs=specs,
        input_groups=input_groups,
        root=root,
        ref=ref,
        entries=entries,
        content_cache=content_cache,
        import_cache=import_cache,
    )
    return _canonical_json_fingerprint(
        OWNER_INPUT_ALGORITHM,
        {
            "owner": spec.id,
            "manifest_fingerprint": generator_manifest_fingerprint(spec),
            "source_entries": {path: entries[path] for path in paths},
        },
    )


def _direct_worktree_identity(
    spec: GeneratorSpec,
    *,
    specs: Sequence[GeneratorSpec],
    input_groups: GeneratedArtifactInputGroups,
    root: Path,
    available_paths: Sequence[str] | None = None,
    import_cache: dict[str, tuple[str, ...]] | None = None,
) -> str:
    paths = resolved_worktree_inputs(
        spec,
        specs=specs,
        input_groups=input_groups,
        root=root,
        available_paths=available_paths,
        import_cache=import_cache,
    )
    return _canonical_json_fingerprint(
        OWNER_INPUT_ALGORITHM,
        {
            "owner": spec.id,
            "manifest_fingerprint": generator_manifest_fingerprint(spec),
            "source_entries": dict(_path_entries(root, paths)),
        },
    )


def affected_owner_plan(
    *,
    base_ref: str,
    root: Path,
) -> dict[str, object]:
    specs = load_manifest(root / MANIFEST_GIT_PATH)
    groups = load_input_groups(root / MANIFEST_GIT_PATH)
    ordered = topological_order(specs)
    cacheable = [spec for spec in ordered if spec.reuse_policy == "safe"]
    reason = "declared-input-change"
    try:
        base_specs, base_groups = _manifest_at_ref(root, base_ref)
        base_by_id = {spec.id: spec for spec in base_specs}
        base_entries = _ref_entries(root, base_ref)
        worktree_paths = _worktree_paths(root)
        worktree_import_cache: dict[str, tuple[str, ...]] = {}
        base_content_cache = _ref_text_cache(
            root,
            base_ref,
            tuple(
                sorted(
                    path
                    for path in base_entries
                    if path.endswith(".py")
                    and PurePosixPath(path).parts[0]
                    in {"quorune", "scripts", "server"}
                    or path == "simctl.py"
                )
            ),
        )
        base_import_cache: dict[str, tuple[str, ...]] = {}
        current_paths = {
            spec.id: resolved_worktree_inputs(
                spec,
                specs=specs,
                input_groups=groups,
                root=root,
                available_paths=worktree_paths,
                import_cache=worktree_import_cache,
            )
            for spec in cacheable
        }
        current_entries = dict(
            _path_entries(
                root,
                tuple(
                    sorted(
                        {
                            path
                            for paths in current_paths.values()
                            for path in paths
                        }
                    )
                ),
            )
        )
        base_paths = {
            spec.id: _resolved_ref_inputs(
                spec,
                specs=base_specs,
                input_groups=base_groups,
                root=root,
                ref=base_ref,
                entries=base_entries,
                content_cache=base_content_cache,
                import_cache=base_import_cache,
            )
            for spec in base_specs
            if spec.id in {candidate.id for candidate in cacheable}
        }
        directly_affected: set[str] = set()
        for spec in cacheable:
            base_spec = base_by_id.get(spec.id)
            if base_spec is None:
                directly_affected.add(spec.id)
                continue
            current_fingerprint = _canonical_json_fingerprint(
                OWNER_INPUT_ALGORITHM,
                {
                    "owner": spec.id,
                    "manifest_fingerprint": generator_manifest_fingerprint(spec),
                    "source_entries": {
                        path: current_entries[path]
                        for path in current_paths[spec.id]
                    },
                },
            )
            base_fingerprint = _canonical_json_fingerprint(
                OWNER_INPUT_ALGORITHM,
                {
                    "owner": base_spec.id,
                    "manifest_fingerprint": generator_manifest_fingerprint(
                        base_spec
                    ),
                    "source_entries": {
                        path: base_entries[path]
                        for path in base_paths[spec.id]
                    },
                },
            )
            if current_fingerprint != base_fingerprint:
                directly_affected.add(spec.id)
    except (GeneratedOwnerCacheError, ValueError):
        directly_affected = {spec.id for spec in cacheable}
        reason = "base-manifest-has-no-input-closure"
    affected = set(directly_affected)
    changed = True
    while changed:
        changed = False
        for spec in cacheable:
            if spec.id not in affected and set(spec.depends_on) & affected:
                affected.add(spec.id)
                changed = True
    selected = [spec for spec in ordered if spec.id in affected]
    stages: dict[str, list[str]] = {}
    for spec in selected:
        stages.setdefault(spec.execution_class, []).append(spec.id)
    return {
        "schema_version": 1,
        "base_ref": base_ref,
        "reason": reason,
        "directly_affected": sorted(directly_affected),
        "owners": [spec.id for spec in selected],
        "earliest_owner": selected[0].id if selected else None,
        "database_required": any(
            spec.database_identity == "pinned-card-database" for spec in selected
        ),
        "stages": stages,
    }


def _assigned_literals(source: str, names: set[str]) -> dict[str, object]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise GeneratedOwnerCacheError(
            "unable to parse compiler identity declaration"
        ) from exc
    result: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value_node = node.value
        if value_node is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in names:
                try:
                    result[target.id] = ast.literal_eval(value_node)
                except (ValueError, TypeError) as exc:
                    raise GeneratedOwnerCacheError(
                        f"compiler identity {target.id} must be a literal"
                    ) from exc
    missing = names - set(result)
    if missing:
        raise GeneratedOwnerCacheError(
            "compiler identity declarations are missing: "
            + ", ".join(sorted(missing))
        )
    return result


def _compiler_identity_values(
    read_text: Callable[[str], str],
) -> dict[str, object]:
    return {
        **_assigned_literals(
            read_text("quorune/oracle_ir.py"),
            {"ORACLE_COMPILER_VERSION", "ORACLE_IR_SCHEMA_VERSION"},
        ),
        **_assigned_literals(
            read_text("quorune/card_programs/model.py"),
            {"CARD_PROGRAM_SCHEMA_VERSION"},
        ),
    }


def compiler_identity_status(
    *,
    base_ref: str,
    root: Path,
) -> dict[str, object]:
    specs = load_manifest(root / MANIFEST_GIT_PATH)
    spec = next(
        candidate for candidate in specs if candidate.id == "compiler-corpus-coverage"
    )
    current_available = set(_worktree_paths(root))
    current_implementation = _python_import_closure(
        _match_patterns(
            tuple(current_available),
            spec.implementation_inputs,
        ),
        available=current_available,
        read_text=lambda relative: (root / relative).read_text(encoding="utf-8"),
        traverse_package_initializers=False,
    )
    current_implementation -= _IDENTITY_IMPLEMENTATION_PATHS
    base_entries = _ref_entries(root, base_ref)
    try:
        base_specs, _base_groups = _manifest_at_ref(root, base_ref)
        base_spec = next(
            candidate
            for candidate in base_specs
            if candidate.id == "compiler-corpus-coverage"
        )
        base_content_cache: dict[str, str] = {}

        def read_base_text(relative: str) -> str:
            content = base_content_cache.get(relative)
            if content is None:
                content = _run_git(
                    root,
                    "show",
                    f"{base_ref}:{relative}",
                ).decode("utf-8", errors="strict")
                base_content_cache[relative] = content
            return content

        base_implementation = _python_import_closure(
            _match_patterns(
                tuple(base_entries),
                base_spec.implementation_inputs,
            ),
            available=set(base_entries),
            read_text=read_base_text,
            traverse_package_initializers=False,
        )
        base_implementation -= _IDENTITY_IMPLEMENTATION_PATHS
    except (GeneratedOwnerCacheError, StopIteration):
        # The schema-v2 migration has no base declarations. The current static
        # implementation closure is still sufficient to compare old blobs.
        base_implementation = {
            path for path in current_implementation if path in base_entries
        }
    compared_paths = sorted(current_implementation | base_implementation)
    current_entries = dict(
        _path_entries(
            root,
            tuple(path for path in compared_paths if (root / path).is_file()),
        )
    )
    semantic_changed = any(
        current_entries.get(path) != base_entries.get(path)
        for path in compared_paths
    )
    current_identity = _compiler_identity_values(
        lambda relative: (root / relative).read_text(encoding="utf-8")
    )
    base_identity = _compiler_identity_values(
        lambda relative: _run_git(root, "show", f"{base_ref}:{relative}").decode(
            "utf-8", errors="strict"
        )
    )
    identity_changed = current_identity != base_identity
    return {
        "schema_version": 1,
        "base_ref": base_ref,
        "semantic_compiler_changed": semantic_changed,
        "compiler_identity_changed": identity_changed,
        "current_identity": current_identity,
        "base_identity": base_identity,
        "ok": not semantic_changed or identity_changed,
    }


__all__ = [
    "DATABASE_IDENTITY_ALGORITHM",
    "GeneratedOwnerCacheError",
    "OWNER_INPUT_ALGORITHM",
    "OWNER_OUTPUT_ALGORITHM",
    "OWNER_RECEIPT_SCHEMA_VERSION",
    "OwnerArtifactReceipt",
    "OwnerInputIdentity",
    "affected_owner_plan",
    "build_owner_receipt",
    "compiler_identity_status",
    "database_builder_input_fingerprint",
    "owner_cache_directory",
    "owner_input_identity",
    "owner_output_fingerprint",
    "pinned_database_identity",
    "read_owner_receipt",
    "resolved_worktree_inputs",
    "restore_owner_artifact",
    "store_owner_artifact",
    "write_owner_receipt",
]
