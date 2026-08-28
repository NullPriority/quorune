from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "platform" / "generated-artifacts.json"
_WRITE_POLICIES = {"automatic", "database", "manual"}
_DATABASE_IDENTITIES = {"none", "pinned-card-database"}
_EXECUTION_CLASSES = {
    "manual",
    "foundation",
    "corpus",
    "fanout",
    "architecture",
    "reusable",
    "compact",
    "scheduler",
}
_REUSE_POLICIES = {"safe", "noncacheable"}
_DEPENDENT_CHANGE_POLICIES = {"inputs", "outputs"}


class GeneratedArtifactManifestError(ValueError):
    """Raised when generated-artifact ownership or ordering is invalid."""


@dataclass(frozen=True)
class GeneratorSpec:
    id: str
    depends_on: tuple[str, ...]
    outputs: tuple[str, ...]
    check: tuple[str, ...]
    write: tuple[str, ...] | None
    write_with_database: tuple[str, ...] | None
    write_policy: str
    input_groups: tuple[str, ...] = ()
    input_paths: tuple[str, ...] = ()
    implementation_inputs: tuple[str, ...] = ()
    database_identity: str = "none"
    execution_class: str = "foundation"
    reuse_policy: str = "noncacheable"
    dependent_change_policy: str = "inputs"


@dataclass(frozen=True)
class GeneratedArtifactDiscoverySpec:
    """Repository signals that identify tracked generated artifacts."""

    path_prefixes: tuple[str, ...]
    path_globs: tuple[str, ...]
    explicit_paths: tuple[str, ...]
    markdown_statuses: tuple[str, ...]
    content_markers: tuple[str, ...]


@dataclass(frozen=True)
class GeneratedArtifactOwnershipReport:
    discovered: tuple[str, ...]
    owners: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class GeneratedArtifactInputGroups:
    """Canonical reusable source-pattern groups declared by the manifest."""

    groups: tuple[tuple[str, tuple[str, ...]], ...]

    def patterns(self, group_id: str) -> tuple[str, ...]:
        return dict(self.groups).get(group_id, ())

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(group_id for group_id, _patterns in self.groups)


def _string_list(value: object, *, field: str, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise GeneratedArtifactManifestError(
            f"{field} must be a list of nonempty strings"
        )
    if not allow_empty and not value:
        raise GeneratedArtifactManifestError(f"{field} must not be empty")
    return tuple(value)


def _optional_command(value: object, *, field: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    return _string_list(value, field=field)


def _canonical_relative_path(value: str, *, field: str) -> str:
    canonical = PurePosixPath(value)
    if (
        "\\" in value
        or canonical.is_absolute()
        or PureWindowsPath(value).is_absolute()
        or ".." in canonical.parts
        or canonical.as_posix() != value
    ):
        raise GeneratedArtifactManifestError(
            f"{field} must be a canonical repository-relative POSIX path: {value}"
        )
    return value


def _canonical_input_pattern(value: str, *, field: str) -> str:
    _canonical_relative_path(value, field=field)
    if value.endswith("/"):
        raise GeneratedArtifactManifestError(
            f"{field} must name a file or file glob: {value}"
        )
    return value


def parse_input_groups(value: Mapping[str, Any]) -> GeneratedArtifactInputGroups:
    if not isinstance(value, Mapping) or not value:
        raise GeneratedArtifactManifestError(
            "generated-artifact input_groups must be a nonempty object"
        )
    rows: list[tuple[str, tuple[str, ...]]] = []
    for group_id, raw_patterns in value.items():
        if (
            type(group_id) is not str
            or not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", group_id)
        ):
            raise GeneratedArtifactManifestError(
                "generated-artifact input group IDs must be stable kebab-case"
            )
        patterns = _string_list(
            raw_patterns,
            field=f"generated-artifact input group {group_id}",
        )
        if len(patterns) != len(set(patterns)):
            raise GeneratedArtifactManifestError(
                f"generated-artifact input group {group_id} contains duplicates"
            )
        for pattern in patterns:
            _canonical_input_pattern(
                pattern,
                field=f"generated-artifact input group {group_id}",
            )
        rows.append((group_id, patterns))
    return GeneratedArtifactInputGroups(groups=tuple(sorted(rows)))


def parse_discovery(
    value: Mapping[str, Any],
) -> GeneratedArtifactDiscoverySpec:
    expected_fields = {
        "path_prefixes",
        "path_globs",
        "explicit_paths",
        "markdown_statuses",
        "content_markers",
    }
    if set(value) != expected_fields:
        raise GeneratedArtifactManifestError(
            "generated-artifact discovery has unknown or missing fields"
        )
    path_prefixes = _string_list(
        value.get("path_prefixes"),
        field="generated-artifact discovery path_prefixes",
    )
    for prefix in path_prefixes:
        if not prefix.endswith("/"):
            raise GeneratedArtifactManifestError(
                "generated-artifact discovery prefixes must end with /"
            )
        _canonical_relative_path(
            prefix[:-1],
            field="generated-artifact discovery prefix",
        )
    path_globs = _string_list(
        value.get("path_globs"),
        field="generated-artifact discovery path_globs",
    )
    for pattern in path_globs:
        _canonical_relative_path(
            pattern,
            field="generated-artifact discovery glob",
        )
    explicit_paths = _string_list(
        value.get("explicit_paths"),
        field="generated-artifact discovery explicit_paths",
    )
    for path in explicit_paths:
        _canonical_relative_path(
            path,
            field="generated-artifact discovery explicit path",
        )
    values = {
        "path_prefixes": path_prefixes,
        "path_globs": path_globs,
        "explicit_paths": explicit_paths,
        "markdown_statuses": _string_list(
            value.get("markdown_statuses"),
            field="generated-artifact discovery markdown_statuses",
        ),
        "content_markers": _string_list(
            value.get("content_markers"),
            field="generated-artifact discovery content_markers",
        ),
    }
    for field, entries in values.items():
        if len(entries) != len(set(entries)):
            raise GeneratedArtifactManifestError(
                f"generated-artifact discovery {field} contains duplicates"
            )
    return GeneratedArtifactDiscoverySpec(**values)


def parse_manifest(value: Mapping[str, Any]) -> tuple[GeneratorSpec, ...]:
    if set(value) != {
        "schema_version",
        "discovery",
        "input_groups",
        "generators",
    }:
        raise GeneratedArtifactManifestError(
            "generated-artifact manifest has unknown or missing top-level fields"
        )
    schema_version = value.get("schema_version")
    if schema_version not in {3, 4}:
        raise GeneratedArtifactManifestError(
            "unsupported generated-artifact manifest schema_version"
        )
    discovery = value.get("discovery")
    if not isinstance(discovery, Mapping):
        raise GeneratedArtifactManifestError(
            "generated-artifact discovery must contain a JSON object"
        )
    parse_discovery(discovery)
    raw_input_groups = value.get("input_groups")
    if not isinstance(raw_input_groups, Mapping):
        raise GeneratedArtifactManifestError(
            "generated-artifact input_groups must contain a JSON object"
        )
    input_groups = parse_input_groups(raw_input_groups)
    rows = value.get("generators")
    if not isinstance(rows, list) or not rows:
        raise GeneratedArtifactManifestError("generators must be a nonempty list")

    expected_fields = {
        "id",
        "depends_on",
        "outputs",
        "check",
        "write",
        "write_with_database",
        "write_policy",
        "input_groups",
        "input_paths",
        "implementation_inputs",
        "database_identity",
        "execution_class",
        "reuse_policy",
    }
    specs: list[GeneratorSpec] = []
    seen_ids: set[str] = set()
    output_owners: dict[str, str] = {}
    for index, row in enumerate(rows):
        if (
            not isinstance(row, Mapping)
            or not expected_fields.issubset(row)
            or set(row) - expected_fields != (
                {"dependent_change_policy"} if schema_version == 4 and "dependent_change_policy" in row else set()
            )
        ):
            raise GeneratedArtifactManifestError(
                f"generators[{index}] has unknown or missing fields"
            )
        generator_id = row.get("id")
        if (
            not isinstance(generator_id, str)
            or not generator_id
            or generator_id in seen_ids
        ):
            raise GeneratedArtifactManifestError(
                f"generators[{index}].id must be unique and nonempty"
            )
        seen_ids.add(generator_id)
        policy = row.get("write_policy")
        if policy not in _WRITE_POLICIES:
            raise GeneratedArtifactManifestError(
                f"generator {generator_id} has unsupported write_policy"
            )
        outputs = _string_list(
            row.get("outputs"), field=f"generator {generator_id} outputs"
        )
        if len(outputs) != len(set(outputs)):
            raise GeneratedArtifactManifestError(
                f"generator {generator_id} declares a duplicate output"
            )
        for output in outputs:
            _canonical_relative_path(
                output,
                field=f"generator {generator_id} output",
            )
            owner = output_owners.get(output)
            if owner is not None:
                raise GeneratedArtifactManifestError(
                    f"generated output {output} has multiple owners: "
                    f"{owner}, {generator_id}"
                )
            output_owners[output] = generator_id
        write = _optional_command(
            row.get("write"), field=f"generator {generator_id} write"
        )
        write_with_database = _optional_command(
            row.get("write_with_database"),
            field=f"generator {generator_id} write_with_database",
        )
        if policy == "automatic" and write is None:
            raise GeneratedArtifactManifestError(
                f"automatic generator {generator_id} requires a write command"
            )
        if policy == "database" and write_with_database is None:
            raise GeneratedArtifactManifestError(
                f"database generator {generator_id} requires write_with_database"
            )
        if write_with_database is not None and "{db}" not in write_with_database:
            raise GeneratedArtifactManifestError(
                f"generator {generator_id} database command must contain {{db}}"
            )
        depends_on = _string_list(
            row.get("depends_on"),
            field=f"generator {generator_id} depends_on",
            allow_empty=True,
        )
        if len(depends_on) != len(set(depends_on)):
            raise GeneratedArtifactManifestError(
                f"generator {generator_id} declares a duplicate dependency"
            )
        declared_groups = _string_list(
            row.get("input_groups"),
            field=f"generator {generator_id} input_groups",
            allow_empty=True,
        )
        if len(declared_groups) != len(set(declared_groups)):
            raise GeneratedArtifactManifestError(
                f"generator {generator_id} declares duplicate input groups"
            )
        unknown_groups = sorted(set(declared_groups) - set(input_groups.ids))
        if unknown_groups:
            raise GeneratedArtifactManifestError(
                f"generator {generator_id} has unknown input groups: "
                + ", ".join(unknown_groups)
            )
        input_paths = _string_list(
            row.get("input_paths"),
            field=f"generator {generator_id} input_paths",
            allow_empty=True,
        )
        implementation_inputs = _string_list(
            row.get("implementation_inputs"),
            field=f"generator {generator_id} implementation_inputs",
            allow_empty=True,
        )
        for field_name, patterns in (
            ("input_paths", input_paths),
            ("implementation_inputs", implementation_inputs),
        ):
            if len(patterns) != len(set(patterns)):
                raise GeneratedArtifactManifestError(
                    f"generator {generator_id} declares duplicate {field_name}"
                )
            for pattern in patterns:
                _canonical_input_pattern(
                    pattern,
                    field=f"generator {generator_id} {field_name}",
                )
        database_identity = str(row.get("database_identity") or "")
        if database_identity not in _DATABASE_IDENTITIES:
            raise GeneratedArtifactManifestError(
                f"generator {generator_id} has unsupported database_identity"
            )
        uses_database = write_with_database is not None
        if uses_database is not (
            database_identity == "pinned-card-database"
        ):
            raise GeneratedArtifactManifestError(
                f"generator {generator_id} database identity contradicts its write command"
            )
        execution_class = str(row.get("execution_class") or "")
        if execution_class not in _EXECUTION_CLASSES:
            raise GeneratedArtifactManifestError(
                f"generator {generator_id} has unsupported execution_class"
            )
        reuse_policy = str(row.get("reuse_policy") or "")
        if reuse_policy not in _REUSE_POLICIES:
            raise GeneratedArtifactManifestError(
                f"generator {generator_id} has unsupported reuse_policy"
            )
        dependent_change_policy = str(
            row.get("dependent_change_policy") or "inputs"
        )
        if dependent_change_policy not in _DEPENDENT_CHANGE_POLICIES:
            raise GeneratedArtifactManifestError(
                f"generator {generator_id} has unsupported dependent_change_policy"
            )
        if reuse_policy == "safe" and (
            policy == "manual"
            or not implementation_inputs
            or not (declared_groups or input_paths)
        ):
            raise GeneratedArtifactManifestError(
                f"cacheable generator {generator_id} requires complete source and implementation inputs"
            )
        specs.append(
            GeneratorSpec(
                id=generator_id,
                depends_on=depends_on,
                outputs=outputs,
                check=_string_list(
                    row.get("check"), field=f"generator {generator_id} check"
                ),
                write=write,
                write_with_database=write_with_database,
                write_policy=str(policy),
                input_groups=declared_groups,
                input_paths=input_paths,
                implementation_inputs=implementation_inputs,
                database_identity=database_identity,
                execution_class=execution_class,
                reuse_policy=reuse_policy,
                dependent_change_policy=dependent_change_policy,
            )
        )

    known = {spec.id for spec in specs}
    for spec in specs:
        unknown = set(spec.depends_on) - known
        if unknown:
            raise GeneratedArtifactManifestError(
                f"generator {spec.id} has unknown dependencies: "
                + ", ".join(sorted(unknown))
            )
        if spec.id in spec.depends_on:
            raise GeneratedArtifactManifestError(
                f"generator {spec.id} cannot depend on itself"
            )
    topological_order(specs)
    return tuple(specs)


def _load_manifest_value(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise GeneratedArtifactManifestError(
            "generated-artifact manifest must contain a JSON object"
        )
    return value


def _tracked_paths(root: Path) -> tuple[str, ...]:
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GeneratedArtifactManifestError(
            "unable to discover tracked generated artifacts with git ls-files"
        ) from exc
    return tuple(
        sorted(
            raw.decode("utf-8", errors="strict")
            for raw in completed.stdout.split(b"\0")
            if raw
        )
    )


def _markdown_has_generated_status(
    prefix: str,
    statuses: Sequence[str],
) -> bool:
    return any(
        re.search(
            rf"(?m)^status:\s*[\"']?{re.escape(status)}[\"']?\s*$",
            prefix,
        )
        for status in statuses
    )


def discover_tracked_generated_artifacts(
    root: Path,
    discovery: GeneratedArtifactDiscoverySpec,
) -> tuple[str, ...]:
    """Discover generated files independently of manifest output claims."""

    generated: list[str] = []
    explicit = set(discovery.explicit_paths)
    globbed = {
        path.relative_to(root).as_posix()
        for pattern in discovery.path_globs
        for path in root.glob(pattern)
        if path.is_file()
    }
    for relative in _tracked_paths(root):
        pure = PurePosixPath(relative)
        matched = (
            relative in explicit
            or any(relative.startswith(prefix) for prefix in discovery.path_prefixes)
            or relative in globbed
        )
        path = root / relative
        prefix = ""
        if not matched and path.is_file():
            try:
                prefix = path.read_bytes()[:4096].decode(
                    "utf-8", errors="strict"
                )
            except UnicodeDecodeError:
                prefix = ""
            matched = any(
                marker in prefix for marker in discovery.content_markers
            )
        if (
            not matched
            and pure.suffix.lower() == ".md"
            and path.is_file()
        ):
            if not prefix:
                try:
                    prefix = path.read_bytes()[:4096].decode(
                        "utf-8", errors="strict"
                    )
                except UnicodeDecodeError:
                    prefix = ""
            matched = _markdown_has_generated_status(
                prefix,
                discovery.markdown_statuses,
            )
        if matched:
            generated.append(relative)
    return tuple(generated)


def validate_manifest_completeness(
    specs: Sequence[GeneratorSpec],
    discovery: GeneratedArtifactDiscoverySpec,
    *,
    root: Path = ROOT,
) -> GeneratedArtifactOwnershipReport:
    """Require every discovered artifact to have one existing manifest owner."""

    root_resolved = root.resolve()
    owner_by_output = {
        output: spec.id
        for spec in specs
        for output in spec.outputs
    }
    missing: list[str] = []
    escaped: list[str] = []
    for output in owner_by_output:
        path = root / output
        try:
            path.resolve(strict=False).relative_to(root_resolved)
        except ValueError:
            escaped.append(output)
            continue
        if not path.is_file():
            missing.append(output)
    if escaped:
        raise GeneratedArtifactManifestError(
            "registered generated outputs resolve outside the repository: "
            + ", ".join(sorted(escaped))
        )
    if missing:
        raise GeneratedArtifactManifestError(
            "registered generated outputs do not exist: "
            + ", ".join(sorted(missing))
        )

    tracked = set(_tracked_paths(root))
    untracked = sorted(set(owner_by_output) - tracked)
    if untracked:
        raise GeneratedArtifactManifestError(
            "registered generated outputs are not tracked by Git: "
            + ", ".join(untracked)
        )

    discovered = discover_tracked_generated_artifacts(root, discovery)
    unowned = sorted(set(discovered) - set(owner_by_output))
    if unowned:
        raise GeneratedArtifactManifestError(
            "tracked generated artifacts have no manifest owner: "
            + ", ".join(unowned)
        )
    undiscovered = sorted(set(owner_by_output) - set(discovered))
    if undiscovered:
        raise GeneratedArtifactManifestError(
            "registered generated outputs have no independent discovery "
            "signal: " + ", ".join(undiscovered)
        )
    return GeneratedArtifactOwnershipReport(
        discovered=discovered,
        owners=tuple(sorted(owner_by_output.items())),
    )


def load_manifest(
    path: Path = MANIFEST_PATH,
    *,
    root: Path = ROOT,
) -> tuple[GeneratorSpec, ...]:
    value = _load_manifest_value(path)
    specs = parse_manifest(value)
    discovery_value = value["discovery"]
    assert isinstance(discovery_value, Mapping)
    validate_manifest_completeness(
        specs,
        parse_discovery(discovery_value),
        root=root,
    )
    return specs


def load_input_groups(
    path: Path = MANIFEST_PATH,
) -> GeneratedArtifactInputGroups:
    value = _load_manifest_value(path)
    raw = value.get("input_groups")
    if not isinstance(raw, Mapping):
        raise GeneratedArtifactManifestError(
            "generated-artifact input_groups must contain a JSON object"
        )
    return parse_input_groups(raw)


def generator_manifest_fingerprint(spec: GeneratorSpec) -> str:
    payload = {
        "id": spec.id,
        "depends_on": list(spec.depends_on),
        "outputs": list(spec.outputs),
        "check": list(spec.check),
        "write": list(spec.write) if spec.write is not None else None,
        "write_with_database": (
            list(spec.write_with_database)
            if spec.write_with_database is not None
            else None
        ),
        "write_policy": spec.write_policy,
        "input_groups": list(spec.input_groups),
        "input_paths": list(spec.input_paths),
        "implementation_inputs": list(spec.implementation_inputs),
        "database_identity": spec.database_identity,
        "execution_class": spec.execution_class,
        "reuse_policy": spec.reuse_policy,
        "dependent_change_policy": spec.dependent_change_policy,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def topological_order(
    specs: Sequence[GeneratorSpec],
) -> tuple[GeneratorSpec, ...]:
    by_id = {spec.id: spec for spec in specs}
    order_index = {spec.id: index for index, spec in enumerate(specs)}
    pending = {spec.id: set(spec.depends_on) for spec in specs}
    ordered: list[GeneratorSpec] = []
    while pending:
        ready = sorted(
            (generator_id for generator_id, dependencies in pending.items() if not dependencies),
            key=order_index.__getitem__,
        )
        if not ready:
            cycle = ", ".join(sorted(pending))
            raise GeneratedArtifactManifestError(
                f"generated-artifact dependency graph contains a cycle: {cycle}"
            )
        for generator_id in ready:
            ordered.append(by_id[generator_id])
            pending.pop(generator_id)
            for dependencies in pending.values():
                dependencies.discard(generator_id)
    return tuple(ordered)


def all_outputs(specs: Sequence[GeneratorSpec]) -> tuple[str, ...]:
    return tuple(output for spec in specs for output in spec.outputs)


def python_command(arguments: Sequence[str]) -> tuple[str, ...]:
    return (str(Path(sys.executable).resolve()), *arguments)


def check_command(spec: GeneratorSpec) -> tuple[str, ...]:
    return python_command(spec.check)


def write_command(
    spec: GeneratorSpec,
    *,
    database: Path | None,
    include_manual: bool,
) -> tuple[str, ...] | None:
    arguments: tuple[str, ...] | None
    if spec.write_policy == "manual" and not include_manual:
        return None
    if database is not None and spec.write_with_database is not None:
        arguments = tuple(
            str(database) if item == "{db}" else item
            for item in spec.write_with_database
        )
    else:
        arguments = spec.write
    return python_command(arguments) if arguments is not None else None


__all__ = [
    "GeneratedArtifactDiscoverySpec",
    "GeneratedArtifactManifestError",
    "GeneratedArtifactOwnershipReport",
    "GeneratorSpec",
    "GeneratedArtifactInputGroups",
    "MANIFEST_PATH",
    "ROOT",
    "all_outputs",
    "check_command",
    "discover_tracked_generated_artifacts",
    "load_manifest",
    "load_input_groups",
    "generator_manifest_fingerprint",
    "parse_input_groups",
    "parse_discovery",
    "parse_manifest",
    "topological_order",
    "validate_manifest_completeness",
    "write_command",
]
