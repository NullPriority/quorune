from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tokenize
import tomllib
import unittest
from typing import Any, Iterable, Mapping

try:
    from scripts.architecture_observability import (
        build_migration_queue,
        build_subsystem_capsules,
        classify_state_writes,
        interaction_assurance_summary,
        runtime_text_accesses,
    )
    from scripts.architecture_identity_flow import (
        analyze_identity_flows,
    )
    from scripts.source_tree_fingerprint import (
        SOURCE_TREE_FINGERPRINT_ALGORITHM,
        tracked_worktree_source_fingerprint,
    )
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    from architecture_observability import (
        build_migration_queue,
        build_subsystem_capsules,
        classify_state_writes,
        interaction_assurance_summary,
        runtime_text_accesses,
    )
    from architecture_identity_flow import (
        analyze_identity_flows,
    )
    from source_tree_fingerprint import (
        SOURCE_TREE_FINGERPRINT_ALGORITHM,
        tracked_worktree_source_fingerprint,
    )


ROOT = Path(__file__).resolve().parents[1]
# Architecture generation must inspect and import the same checkout.  This is
# especially important when the command runs from a secondary Git worktree
# using a virtual environment whose editable install points at another one.
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)
SOURCE = ROOT / "platform" / "architecture-audit-source.json"
JSON_OUTPUT = ROOT / "coverage" / "architecture-audit.json"
ARCHITECTURE_STATUS = ROOT / "docs" / "ARCHITECTURE_DEBT_STATUS.md"
COMPILER_STATUS = ROOT / "docs" / "COMPILER_COVERAGE_STATUS.md"
GUARD_BASELINE = ROOT / "platform" / "architecture-guard-baseline.json"
ARCHITECTURE_POLICY = ROOT / "platform" / "architecture-policy.json"
MODULE_CLASSIFICATIONS = ROOT / "platform" / "module-classifications.json"
ARCHITECTURE_EXCEPTIONS = ROOT / "platform" / "architecture-exceptions.json"
CAPABILITY_REGISTRY = (
    ROOT / "quorune" / "rules" / "capability-registry.json"
)
CARD_PROGRAM_SCHEMA = ROOT / "schemas" / "card-program-v2.schema.json"
CAPABILITY_EVIDENCE = (
    ROOT / "quorune" / "rules" / "capability-evidence.json"
)
CONTINUOUS_PERFORMANCE_BASELINE = (
    ROOT / "platform" / "continuous-effect-performance-baseline.json"
)

PYTHON_SUFFIXES = {".py"}
WEB_SUFFIXES = {".ts", ".tsx", ".css"}
MUTATING_METHODS = {
    "add",
    "append",
    "clear",
    "discard",
    "extend",
    "insert",
    "pop",
    "remove",
    "reverse",
    "setdefault",
    "sort",
    "update",
}
UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
)
DOC_METADATA_KEYS = {
    "title",
    "status",
    "authoritative_source",
    "verified",
    "audience",
    "maintenance",
}
GENERATED_VERIFIED_SENTINEL = "validated-by-owning-generator"
VIRTUAL_GENERATED_DOCS = {
    "docs/ARCHITECTURE_DEBT_STATUS.md": {
        "title": "Architecture debt status",
        "status": "generated",
        "authoritative_source": "coverage/architecture-audit.json",
        "verified": "generated from current architecture sources",
        "audience": "maintainers and rules contributors",
        "maintenance": "generated",
    },
    "docs/COMPILER_COVERAGE_STATUS.md": {
        "title": "Compiler coverage status",
        "status": "generated",
        "authoritative_source": "coverage/architecture-audit.json",
        "verified": "generated from pinned coverage artifacts",
        "audience": "compiler and rules contributors",
        "maintenance": "generated",
    },
}


@dataclass(frozen=True)
class SourceAnalysis:
    relative: str
    module: str
    text: str
    tree: ast.Module
    logical_lines: frozenset[int]
    functions: tuple[dict[str, Any], ...]
    imports: tuple[str, ...]
    string_literals: tuple[dict[str, Any], ...]
    state_writes: tuple[dict[str, Any], ...]
    semantic_branches: tuple[dict[str, Any], ...]
    oracle_id_literals: tuple[dict[str, Any], ...]


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _serialize_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _source() -> dict[str, Any]:
    source = _load_json(SOURCE)
    if source.get("schema_version") != 3:
        raise ValueError("Unsupported architecture audit source schema")
    observations = source.get("historical_observations")
    if not isinstance(observations, list) or not observations:
        raise ValueError("Architecture audit source requires historical observations")
    if not isinstance(source.get("program"), dict):
        raise ValueError("Architecture audit source requires stable program policy")
    return source


def _historical_observation(source: Mapping[str, Any]) -> Mapping[str, Any]:
    return source["historical_observations"][-1]


def _production_paths(source: Mapping[str, Any]) -> list[Path]:
    paths: set[Path] = set()
    for relative in source["scope"]["production_roots"]:
        root = ROOT / relative
        if not root.is_dir():
            raise ValueError(f"Production root does not exist: {relative}")
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in PYTHON_SUFFIXES | WEB_SUFFIXES:
                if "generated" not in path.relative_to(ROOT).parts:
                    paths.add(path)
    for relative in source["scope"].get("production_files", []):
        path = ROOT / relative
        if not path.is_file():
            raise ValueError(f"Production file does not exist: {relative}")
        paths.add(path)
    return sorted(paths, key=lambda value: value.relative_to(ROOT).as_posix())


def _logical_python_lines(text: str) -> frozenset[int]:
    ignored = {
        tokenize.ENCODING,
        tokenize.ENDMARKER,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.NEWLINE,
        tokenize.NL,
        tokenize.COMMENT,
    }
    lines: set[int] = set()
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type not in ignored and token.string.strip():
            lines.add(token.start[0])
    return frozenset(lines)


def _logical_web_lines(text: str) -> int:
    in_block = False
    count = 0
    for raw in text.splitlines():
        line = raw.strip()
        if in_block:
            if "*/" in line:
                in_block = False
                line = line.split("*/", 1)[1].strip()
            else:
                continue
        if line.startswith("/*"):
            if "*/" not in line[2:]:
                in_block = True
                continue
            line = line.split("*/", 1)[1].strip()
        if line and not line.startswith("//"):
            count += 1
    return count


def _module_name(relative: str) -> str:
    path = Path(relative).with_suffix("")
    parts = list(path.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _source_segment(lines: tuple[str, ...], node: ast.AST) -> str:
    """Return a node's source without repeatedly rescanning the whole module."""
    start_line = getattr(node, "lineno", None)
    end_line = getattr(node, "end_lineno", None)
    start_column = getattr(node, "col_offset", None)
    end_column = getattr(node, "end_col_offset", None)
    if None in {start_line, end_line, start_column, end_column}:
        return ast.unparse(node)
    start_index = int(start_line) - 1
    end_index = int(end_line) - 1
    if start_index == end_index:
        return lines[start_index][int(start_column) : int(end_column)]
    pieces = [lines[start_index][int(start_column) :]]
    pieces.extend(lines[start_index + 1 : end_index])
    pieces.append(lines[end_index][: int(end_column)])
    return "".join(pieces)


def _nearest_function(node: ast.AST, parents: Mapping[ast.AST, ast.AST]) -> str | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
        current = parents.get(current)
    return None


def _function_records(
    tree: ast.Module,
    logical_lines: frozenset[int],
    relative: str,
    parents: Mapping[ast.AST, ast.AST],
) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parent = parents.get(node)
        is_method = isinstance(parent, ast.ClassDef)
        qualified = f"{parent.name}.{node.name}" if is_method else node.name
        end = int(node.end_lineno or node.lineno)
        records.append(
            {
                "file": relative,
                "symbol": qualified,
                "name": node.name,
                "kind": "method" if is_method else "function",
                "visibility": (
                    "dunder"
                    if node.name.startswith("__") and node.name.endswith("__")
                    else "private"
                    if node.name.startswith("_")
                    else "public"
                ),
                "line": node.lineno,
                "end_line": end,
                "physical_lines": end - node.lineno + 1,
                "logical_lines": sum(node.lineno <= line <= end for line in logical_lines),
            }
        )
    return tuple(sorted(records, key=lambda item: (item["line"], item["symbol"])))


def _resolved_imports(
    tree: ast.Module, module: str, *, is_package: bool = False
) -> tuple[str, ...]:
    imports: set[str] = set()
    current = module.split(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package = current if is_package else current[:-1]
                trim = node.level - 1
                prefix = package[: -trim] if trim else package
                if node.module:
                    imports.add(".".join([*prefix, node.module]))
                else:
                    imports.update(".".join([*prefix, alias.name]) for alias in node.names)
            elif node.module:
                imports.add(node.module)
    return tuple(sorted(value for value in imports if value))


def _attribute_chain(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [*_attribute_chain(node.value), node.attr]
    if isinstance(node, ast.Subscript):
        return _attribute_chain(node.value)
    return []


def _is_state_reference(
    node: ast.AST,
    relative: str,
    state_owner_modules: set[str],
    state_parameter_modules: set[str],
) -> bool:
    chain = _attribute_chain(node)
    if relative in state_owner_modules and chain[:2] == ["self", "state"]:
        return True
    return relative in state_parameter_modules and chain[:1] == ["state"]


def _state_write_records(
    tree: ast.Module,
    lines: tuple[str, ...],
    relative: str,
    source: Mapping[str, Any],
    parents: Mapping[ast.AST, ast.AST],
) -> tuple[dict[str, Any], ...]:
    owner_modules = set(source["scope"]["state_owner_modules"])
    parameter_modules = set(source["scope"]["state_parameter_modules"])
    records: dict[tuple[int, int, str, str], dict[str, Any]] = {}

    def state_path(target: ast.AST) -> str:
        chain = _attribute_chain(target)
        if chain[:2] == ["self", "state"]:
            chain = chain[2:]
        elif chain[:1] == ["state"]:
            chain = chain[1:]
        return ".".join(chain) or "<state-root>"

    def add(node: ast.AST, kind: str, target: ast.AST) -> None:
        if not _is_state_reference(target, relative, owner_modules, parameter_modules):
            return
        expression = _source_segment(lines, node)
        expression = " ".join(expression.split())[:240]
        key = (node.lineno, node.col_offset, kind, expression)
        records[key] = {
            "file": relative,
            "line": node.lineno,
            "column": node.col_offset,
            "symbol": _nearest_function(node, parents),
            "kind": kind,
            "state_path": state_path(target),
            "expression": expression,
        }

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                add(node, "assignment", target)
        elif isinstance(node, ast.AnnAssign):
            add(node, "annotated_assignment", node.target)
        elif isinstance(node, ast.AugAssign):
            add(node, "augmented_assignment", node.target)
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                add(node, "delete", target)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in MUTATING_METHODS:
                add(node, f"mutating_call:{node.func.attr}", node.func.value)
    return tuple(records[key] for key in sorted(records))


def _semantic_branch_records(
    tree: ast.Module,
    lines: tuple[str, ...],
    relative: str,
    parents: Mapping[ast.AST, ast.AST],
) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        condition: ast.AST | None = None
        if isinstance(node, ast.If):
            condition = node.test
        elif isinstance(node, ast.Match):
            condition = node.subject
        if condition is None:
            continue
        rendered = _source_segment(lines, condition)
        if not re.search(r"\b(?:op|operation)\b", rendered, re.IGNORECASE):
            continue
        literals = sorted(
            {
                child.value
                for child in ast.walk(condition)
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            }
        )
        records.append(
            {
                "file": relative,
                "line": node.lineno,
                "symbol": _nearest_function(node, parents),
                "condition": " ".join(rendered.split())[:240],
                "string_literals": literals,
            }
        )
    return tuple(sorted(records, key=lambda item: (item["line"], item["condition"])))


def _string_records(
    tree: ast.Module,
    relative: str,
    parents: Mapping[ast.AST, ast.AST],
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    condition_nodes: set[ast.AST] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            condition_nodes.update(ast.walk(node.test))
        elif isinstance(node, ast.Match):
            condition_nodes.update(ast.walk(node.subject))
            for case in node.cases:
                condition_nodes.update(ast.walk(case.pattern))
                if case.guard:
                    condition_nodes.update(ast.walk(case.guard))
    strings: list[dict[str, Any]] = []
    oracle_ids: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        record = {
            "file": relative,
            "line": node.lineno,
            "column": node.col_offset,
            "symbol": _nearest_function(node, parents),
            "value": node.value,
            "in_condition": node in condition_nodes,
        }
        strings.append(record)
        for oracle_id in UUID_PATTERN.findall(node.value):
            oracle_ids.append({**record, "oracle_id": oracle_id.lower()})
    key = lambda item: (item["line"], item["column"], item["value"])
    return tuple(sorted(strings, key=key)), tuple(sorted(oracle_ids, key=key))


def _analyze_python(path: Path, source: Mapping[str, Any]) -> SourceAnalysis:
    relative = path.relative_to(ROOT).as_posix()
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=relative)
    parents = _parent_map(tree)
    lines = tuple(text.splitlines(keepends=True))
    logical = _logical_python_lines(text)
    strings, oracle_ids = _string_records(tree, relative, parents)
    return SourceAnalysis(
        relative=relative,
        module=_module_name(relative),
        text=text,
        tree=tree,
        logical_lines=logical,
        functions=_function_records(tree, logical, relative, parents),
        imports=_resolved_imports(
            tree,
            _module_name(relative),
            is_package=relative.endswith("/__init__.py"),
        ),
        string_literals=strings,
        state_writes=_state_write_records(tree, lines, relative, source, parents),
        semantic_branches=_semantic_branch_records(tree, lines, relative, parents),
        oracle_id_literals=oracle_ids,
    )


def analyze_production() -> tuple[
    dict[str, Any], list[Path], dict[str, SourceAnalysis]
]:
    source = _source()
    paths = _production_paths(source)
    analyses = {
        path.relative_to(ROOT).as_posix(): _analyze_python(path, source)
        for path in paths
        if path.suffix == ".py"
    }
    return source, paths, analyses


def _production_metrics(
    paths: Iterable[Path],
    analyses: Mapping[str, SourceAnalysis],
    source: Mapping[str, Any],
) -> dict[str, Any]:
    modules: list[dict[str, Any]] = []
    functions = [item for value in analyses.values() for item in value.functions]
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        physical = len(text.splitlines())
        logical = (
            len(analyses[relative].logical_lines)
            if relative in analyses
            else _logical_web_lines(text)
        )
        modules.append(
            {
                "file": relative,
                "language": "python" if path.suffix == ".py" else "web",
                "physical_lines": physical,
                "logical_lines": logical,
                "functions_and_methods": len(analyses[relative].functions)
                if relative in analyses
                else None,
            }
        )
    modules.sort(key=lambda item: (-item["logical_lines"], item["file"]))
    thresholds = source["thresholds"]
    method_visibility = Counter(
        item["visibility"] for item in functions if item["kind"] == "method"
    )
    oversized_modules = [
        item
        for item in modules
        if item["logical_lines"] > thresholds["module_logical_lines_review"]
    ]
    oversized_functions = [
        item
        for item in functions
        if item["logical_lines"] > thresholds["function_logical_lines_review"]
    ]
    return {
        "scope": {
            "roots": list(source["scope"]["production_roots"]),
            "files": list(source["scope"].get("production_files", [])),
            "generated_web_types_excluded": True,
            "logical_line_definition": (
                "Python token-bearing lines; nonblank non-comment lines for TypeScript, "
                "TSX, and CSS."
            ),
        },
        "file_count": len(modules),
        "python_file_count": sum(item["language"] == "python" for item in modules),
        "web_file_count": sum(item["language"] == "web" for item in modules),
        "physical_lines": sum(item["physical_lines"] for item in modules),
        "logical_lines": sum(item["logical_lines"] for item in modules),
        "modules": modules,
        "methods": {
            "public": method_visibility["public"],
            "private": method_visibility["private"],
            "dunder": method_visibility["dunder"],
            "total": sum(method_visibility.values()),
        },
        "top_level_functions": sum(item["kind"] == "function" for item in functions),
        "largest_functions_and_methods": sorted(
            functions,
            key=lambda item: (-item["logical_lines"], item["file"], item["line"]),
        )[:30],
        "review_thresholds": thresholds,
        "oversized_module_count": len(oversized_modules),
        "oversized_modules": oversized_modules,
        "oversized_function_and_method_count": len(oversized_functions),
        "oversized_functions_and_methods": sorted(
            oversized_functions,
            key=lambda item: (-item["logical_lines"], item["file"], item["line"]),
        ),
    }


def _import_metrics(analyses: Mapping[str, SourceAnalysis]) -> dict[str, Any]:
    known = {analysis.module for analysis in analyses.values()}
    edges: set[tuple[str, str]] = set()
    for analysis in analyses.values():
        for imported in analysis.imports:
            target = imported
            while target and target not in known and "." in target:
                target = target.rsplit(".", 1)[0]
            if target in known and target != analysis.module:
                edges.add((analysis.module, target))
    fan_in: Counter[str] = Counter(target for _, target in edges)
    fan_out: Counter[str] = Counter(source for source, _ in edges)
    rows = []
    for module in sorted(known):
        rows.append(
            {
                "module": module,
                "fan_in": fan_in[module],
                "fan_out": fan_out[module],
            }
        )
    return {
        "internal_edges": len(edges),
        "modules": rows,
        "highest_fan_in": sorted(rows, key=lambda row: (-row["fan_in"], row["module"]))[:15],
        "highest_fan_out": sorted(rows, key=lambda row: (-row["fan_out"], row["module"]))[:15],
    }


def _engine_metrics(
    analyses: Mapping[str, SourceAnalysis], source: Mapping[str, Any]
) -> dict[str, Any]:
    engine = analyses["quorune/engine.py"]
    methods = [item for item in engine.functions if item["kind"] == "method"]
    responsibilities: list[dict[str, Any]] = []
    assigned: set[str] = set()
    for category in source["engine_method_responsibilities"]:
        pattern = re.compile(category["pattern"], re.IGNORECASE)
        names = sorted(
            item["name"]
            for item in methods
            if item["name"] not in assigned and pattern.search(item["name"])
        )
        assigned.update(names)
        responsibilities.append(
            {"id": category["id"], "method_count": len(names), "methods": names}
        )
    visibility = Counter(item["visibility"] for item in methods)
    return {
        "physical_lines": len(engine.text.splitlines()),
        "logical_lines": len(engine.logical_lines),
        "public_methods": visibility["public"],
        "private_methods": visibility["private"],
        "dunder_methods": visibility["dunder"],
        "responsibility_groups": responsibilities,
        "cross_subsystem_responsibility_count": sum(
            bool(item["method_count"]) for item in responsibilities
        ),
        "unclassified_methods": sorted(
            item["name"] for item in methods if item["name"] not in assigned
        ),
    }


def _state_and_dispatch_metrics(
    analyses: Mapping[str, SourceAnalysis], source: Mapping[str, Any]
) -> dict[str, Any]:
    state_writes = [item for value in analyses.values() for item in value.state_writes]
    branches = [item for value in analyses.values() for item in value.semantic_branches]
    oracle_ids = [item for value in analyses.values() for item in value.oracle_id_literals]
    card_ops = set(source["card_specific_semantic_operations"])
    branch_literals = Counter(
        literal for branch in branches for literal in branch["string_literals"]
    )
    return {
        "direct_game_state_write_heuristic": {
            "count": len(state_writes),
            "locations": sorted(
                state_writes, key=lambda item: (item["file"], item["line"], item["column"])
            ),
            "limitations": (
                "Counts direct assignments and common mutator calls rooted at configured "
                "GameState owners or GameState parameters; alias-mediated writes require review."
            ),
        },
        "semantic_operation_branches": {
            "count": len(branches),
            "locations": sorted(branches, key=lambda item: (item["file"], item["line"])),
            "operation_literal_counts": dict(sorted(branch_literals.items())),
            "card_specific_operation_branch_occurrences": {
                operation: branch_literals[operation]
                for operation in sorted(card_ops)
                if branch_literals[operation]
            },
            "limitations": (
                "AST heuristic covers if/match conditions whose source names op or operation."
            ),
        },
        "oracle_id_literals": {
            "count": len(oracle_ids),
            "locations": sorted(
                oracle_ids, key=lambda item: (item["file"], item["line"], item["column"])
            ),
        },
    }


def _semantic_handler_metrics(
    state_dispatch: Mapping[str, Any],
) -> dict[str, Any]:
    from quorune.semantic_runtime import (
        default_semantic_handler_registry,
        runtime_component_inventory,
        runtime_component_registry_fingerprint,
    )

    registry = default_semantic_handler_registry()
    inventory = registry.inventory()
    runtime_inventory = runtime_component_inventory()
    engine_branches = [
        branch
        for branch in state_dispatch["semantic_operation_branches"][
            "locations"
        ]
        if branch["file"] == "quorune/engine.py"
    ]
    legacy_apply_effect_branches = [
        branch
        for branch in engine_branches
        if branch["symbol"] == "apply_effect"
    ]
    engine_dispatch_operations = {
        literal
        for branch in engine_branches
        for literal in branch["string_literals"]
    }
    registered_operations = {
        str(handler["operation"]) for handler in inventory
    }
    return {
        "schema_version": 1,
        "registry_fingerprint": registry.fingerprint,
        "registered_handler_count": len(inventory),
        "registered_operation_count": len(registered_operations),
        "registered_operations": sorted(registered_operations),
        "handlers": inventory,
        "runtime_registry_fingerprint": (
            runtime_component_registry_fingerprint()
        ),
        "registered_runtime_handler_count": len(runtime_inventory),
        "runtime_handlers": runtime_inventory,
        "engine_string_dispatch_branch_count": len(engine_branches),
        "legacy_apply_effect_branch_count": len(
            legacy_apply_effect_branches
        ),
        "registered_operations_still_in_legacy_dispatch": sorted(
            registered_operations & engine_dispatch_operations
        ),
        "read_only_context": (
            "quorune.semantic_runtime.context."
            "ReadOnlyHandlerContext"
        ),
        "typed_intent_executor": (
            "quorune.semantic_runtime.executor."
            "execute_intent_plan"
        ),
    }


def _debt_trend(
    production: Mapping[str, Any],
    engine: Mapping[str, Any],
    state_dispatch: Mapping[str, Any],
    identity_flow: Mapping[str, Any],
    source: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not GUARD_BASELINE.is_file():
        return None
    baseline = _load_json(GUARD_BASELINE)
    dimensions = {
        "engine_logical_lines": (
            int(baseline["engine"]["logical_lines"]),
            int(engine["logical_lines"]),
        ),
        "direct_game_state_writes": (
            sum(baseline["direct_game_state_writes_by_file"].values()),
            int(state_dispatch["direct_game_state_write_heuristic"]["count"]),
        ),
        "prohibited_identity_dispatch_count": (
            0,
            int(identity_flow["counts"]["prohibited_identity_dispatch_count"]),
        ),
        "oracle_id_literals": (
            len(baseline["oracle_id_literals"]),
            int(state_dispatch["oracle_id_literals"]["count"]),
        ),
        "legacy_card_specific_operations": (
            len(baseline["legacy_card_specific_operations"]),
            len(source["card_specific_semantic_operations"]),
        ),
        "card_named_helpers": (
            len(baseline["card_named_helpers"]),
            len(source["card_named_helpers"]),
        ),
        "oversized_modules": (
            len(baseline["oversized_modules"]),
            int(production["oversized_module_count"]),
        ),
        "oversized_functions_and_methods": (
            len(baseline["oversized_functions_and_methods"]),
            int(production["oversized_function_and_method_count"]),
        ),
    }
    return {
        "baseline_commit": baseline["baseline_commit"],
        "policy": "platform/architecture-policy.json",
        "guard": "python scripts/validate_architecture.py --check",
        "dimensions": {
            name: {
                "baseline": baseline_value,
                "current": current_value,
                "delta": current_value - baseline_value,
            }
            for name, (baseline_value, current_value) in dimensions.items()
        },
    }


def _walk_operations(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        if isinstance(value.get("op"), str):
            yield value["op"]
        for child in value.values():
            yield from _walk_operations(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_operations(child)


def _semantic_pack_metrics(source: Mapping[str, Any]) -> dict[str, Any]:
    files = sorted((ROOT / "quorune" / "semantic_packs").glob("*.json"))
    programs: list[tuple[str, dict[str, Any]]] = []
    pack_rows: list[dict[str, Any]] = []
    operations: Counter[str] = Counter()
    for path in files:
        value = _load_json(path)
        pack_programs = value.get("programs", [])
        if not isinstance(pack_programs, list):
            raise ValueError(f"{path} programs must be a list")
        for program in pack_programs:
            if not isinstance(program, dict):
                raise ValueError(f"{path} contains a non-object program")
            programs.append((path.name, program))
            operations.update(_walk_operations(program))
        pack_rows.append(
            {
                "file": path.relative_to(ROOT).as_posix(),
                "name": value.get("name"),
                "schema_version": value.get("schema_version"),
                "program_count": len(pack_programs),
            }
        )
    keys: defaultdict[str, list[str]] = defaultdict(list)
    oracle_ids: set[str] = set()
    trust = Counter()
    authored_by = Counter()
    for filename, program in programs:
        keys[str(program.get("key"))].append(filename)
        if program.get("oracle_id"):
            oracle_ids.add(str(program["oracle_id"]))
        trust[str(program.get("trust_level") or "missing")] += 1
        provenance = program.get("provenance")
        if isinstance(provenance, dict):
            authored_by[str(provenance.get("authored_by") or "missing")] += 1
    duplicates = {
        key: origins for key, origins in sorted(keys.items()) if len(origins) > 1
    }
    card_specific = set(source["card_specific_semantic_operations"])
    observed = set(operations)
    return {
        "pack_count": len(pack_rows),
        "program_entries": len(programs),
        "unique_program_keys": len(keys),
        "unique_oracle_ids": len(oracle_ids),
        "duplicate_key_count": len(duplicates),
        "duplicate_keys_and_pack_order": duplicates,
        "trust_level_counts": dict(sorted(trust.items())),
        "authored_by_counts": dict(sorted(authored_by.items())),
        "packs": pack_rows,
        "semantic_operation_counts": dict(sorted(operations.items())),
        "card_specific_operations": sorted(card_specific & observed),
        "configured_card_specific_operations_not_observed": sorted(card_specific - observed),
        "unclassified_operation_count": len(observed - card_specific),
        "typed_card_override_boundary_present": (
            ROOT / "quorune" / "card_programs" / "model.py"
        ).is_file(),
        "explicit_typed_override_count": 0,
    }


def _compiler_metrics(
    source: Mapping[str, Any], analyses: Mapping[str, SourceAnalysis]
) -> dict[str, Any]:
    oracle = _load_json(ROOT / "coverage" / "oracle-coverage.json")
    commander = _load_json(ROOT / "coverage" / "oracle-coverage-commander.json")
    mechanics = _load_json(ROOT / "coverage" / "mechanics-coverage.json")
    capabilities = _load_json(CAPABILITY_REGISTRY)
    capability_evidence = _load_json(CAPABILITY_EVIDENCE)
    card_program_schema = _load_json(CARD_PROGRAM_SCHEMA)
    from quorune.semantics import SemanticRegistry

    semantic_card_programs = SemanticRegistry().card_programs()

    def target_effect_assurance_summary(
        report: Mapping[str, Any],
    ) -> dict[str, Any]:
        assurance = report.get("target_effect_corpus_assurance")
        if not isinstance(assurance, Mapping):
            return {"present": False}
        return {
            "present": True,
            "fingerprint": assurance.get("fingerprint"),
            "grammar_source_fingerprint": assurance.get(
                "grammar_source_fingerprint"
            ),
            "identity_fingerprint": assurance.get("identity_fingerprint"),
            "total_nodes": assurance.get("total_nodes"),
            "total_cards": assurance.get("total_cards"),
            "exact_nodes": assurance.get("exact_nodes"),
            "exact_cards_with_template": assurance.get(
                "exact_cards_with_template"
            ),
            "shape_count": assurance.get("shape_count"),
            "contract_fingerprint": (
                assurance.get("synthetic_contract", {}).get("fingerprint")
                if isinstance(assurance.get("synthetic_contract"), Mapping)
                else None
            ),
        }
    trust_basis_counts = Counter(
        str(program.trust_closure["trust_basis"])
        for program in semantic_card_programs
    )
    capability_rows = {
        str(row["id"]): row for row in capabilities["capabilities"]
    }
    capability_statuses = Counter(
        str(row["status"]) for row in capability_rows.values()
    )
    dependency_statuses = Counter(
        str(row["dependency_fail_closed_status"])
        for row in capability_rows.values()
    )
    implementation_mutation_statuses = Counter(
        str(row["implementation_mutation_status"])
        for row in capability_rows.values()
    )
    evidence_classes = Counter(
        str(row["evidence_class"])
        for row in capability_evidence["declarations"]
    )
    aggregate_rows = []
    for aggregate in capabilities["aggregates"]:
        referenced = [
            capability_rows[str(capability_id)]
            for capability_id in aggregate["capabilities"]
        ]
        aggregate_rows.append(
            {
                "mechanic_id": aggregate["mechanic_id"],
                "trusted": all(
                    row["status"] == "trusted" for row in referenced
                ),
                "capability_count": len(referenced),
                "blocked_capabilities": sorted(
                    str(row["id"])
                    for row in referenced
                    if row["status"] != "trusted"
                ),
            }
        )
    capability_fingerprint = hashlib.sha256(
        json.dumps(
            capabilities,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    symbols: defaultdict[str, list[str]] = defaultdict(list)
    for analysis in analyses.values():
        for node in ast.walk(analysis.tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols[node.name].append(f"{analysis.relative}:{node.lineno}")
        for node in analysis.tree.body:
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    symbols[alias.asname or alias.name].append(f"{analysis.relative}:{node.lineno}")
    stages = []
    for stage in source["compiler_stages"]:
        evidence = {
            symbol: sorted(symbols.get(symbol, []))
            for symbol in stage["evidence_symbols"]
        }
        stages.append(
            {
                **stage,
                "evidence": evidence,
                "all_configured_evidence_present": all(evidence.values()) if evidence else False,
            }
        )
    return {
        "current_ir": (
            "OracleCardIR lowered to canonical CardProgram V2 with a derived "
            "SemanticProgram compatibility index"
        ),
        "card_program_v2_present": "CardProgram" in symbols,
        "card_program": {
            "schema_version": card_program_schema["properties"]["schema_version"]["const"],
            "required_card_fields": len(card_program_schema["required"]),
            "required_ability_fields": len(
                card_program_schema["$defs"]["ability"]["required"]
            ),
            "schema": CARD_PROGRAM_SCHEMA.relative_to(ROOT).as_posix(),
            "model": "quorune/card_programs/model.py",
            "adapter": "quorune/card_programs/adapters.py",
            "validator": "quorune/card_programs/validation.py",
            "semantic_registry_program_count": len(semantic_card_programs),
            "trust_basis_counts": dict(sorted(trust_basis_counts.items())),
            "strict_capability_ready_count": sum(
                program.trust_closure["strict_capability_ready"] is True
                for program in semantic_card_programs
            ),
        },
        "compiler_version": oracle.get("compiler_version"),
        "compiler_module": "quorune/oracle_ir.py",
        "compiler_module_physical_lines": len(
            analyses["quorune/oracle_ir.py"].text.splitlines()
        ),
        "compiler_module_logical_lines": len(
            analyses["quorune/oracle_ir.py"].logical_lines
        ),
        "stages": stages,
        "target_effect_corpus_assurance": {
            "full": target_effect_assurance_summary(oracle),
            "commander": target_effect_assurance_summary(commander),
            "source": "coverage/oracle-coverage-commander.json",
        },
        "full_oracle": {
            "snapshot": oracle.get("card_data_snapshot"),
            "total_oracle_ids": oracle.get("total_oracle_ids"),
            "total_faces": oracle.get("total_faces"),
            "status_counts": oracle.get("status_counts"),
            "exact_fraction": oracle.get("exact_fraction"),
            "material_residuals": oracle.get("material_residuals"),
            "residual_kinds": oracle.get("residual_kinds"),
            "template_count": len(oracle.get("templates", {})),
            "current_snapshot_complete": oracle.get("current_snapshot_complete"),
        },
        "commander_legal_oracle": {
            "snapshot": commander.get("card_data_snapshot"),
            "total_oracle_ids": commander.get("total_oracle_ids"),
            "total_faces": commander.get("total_faces"),
            "status_counts": commander.get("status_counts"),
            "exact_fraction": commander.get("exact_fraction"),
            "material_residuals": commander.get("material_residuals"),
            "residual_kinds": commander.get("residual_kinds"),
            "template_count": len(commander.get("templates", {})),
            "current_snapshot_complete": commander.get("current_snapshot_complete"),
        },
        "mechanic_contracts": {
            "total": mechanics.get("total_mechanics"),
            "trusted": mechanics.get("trusted_mechanics"),
            "status_counts": mechanics.get("status_counts"),
            "current_snapshot_complete": mechanics.get("current_snapshot_complete"),
        },
        "rule_capabilities": {
            "schema_version": capabilities["schema_version"],
            "registry_version": capabilities["registry_version"],
            "effective_date": capabilities["effective_date"],
            "source_sha256": capabilities["source_sha256"],
            "fingerprint": capability_fingerprint,
            "total": len(capability_rows),
            "status_counts": dict(sorted(capability_statuses.items())),
            "dependency_fail_closed_status_counts": dict(
                sorted(dependency_statuses.items())
            ),
            "implementation_mutation_status_counts": dict(
                sorted(implementation_mutation_statuses.items())
            ),
            "evidence": {
                "fingerprint": capability_evidence["fingerprint"],
                "registry_fingerprint": capability_evidence[
                    "registry_fingerprint"
                ],
                "declaration_source_fingerprint": capability_evidence[
                    "declaration_source_fingerprint"
                ],
                "declaration_count": len(
                    capability_evidence["declarations"]
                ),
                "class_counts": dict(sorted(evidence_classes.items())),
            },
            "profiles": capabilities["profiles"],
            "aggregates": aggregate_rows,
        },
    }


def _front_matter(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    metadata: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return metadata
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip().strip('"')
    return {}


def _documentation_metrics(source: Mapping[str, Any]) -> dict[str, Any]:
    rows = []
    for relative in source["required_documents"]:
        virtual = VIRTUAL_GENERATED_DOCS.get(relative)
        path = ROOT / relative
        present = path.is_file() or virtual is not None
        metadata = dict(virtual or _front_matter(path))
        if virtual:
            metadata["verified"] = str(
                _historical_observation(source)["baseline_main_commit"]
            )
        if metadata.get("status") == "generated" and metadata.get("verified"):
            metadata["verified"] = GENERATED_VERIFIED_SENTINEL
        missing_metadata = sorted(DOC_METADATA_KEYS - set(metadata)) if present else []
        rows.append(
            {
                "file": relative,
                "present": present,
                "metadata": metadata,
                "missing_metadata": missing_metadata,
                "metadata_complete": present and not missing_metadata,
            }
        )
    return {
        "required_count": len(rows),
        "present_count": sum(row["present"] for row in rows),
        "missing_count": sum(not row["present"] for row in rows),
        "metadata_complete_count": sum(row["metadata_complete"] for row in rows),
        "missing_documents": [row["file"] for row in rows if not row["present"]],
        "metadata_drift": [
            {"file": row["file"], "missing_metadata": row["missing_metadata"]}
            for row in rows
            if row["present"] and row["missing_metadata"]
        ],
        "policy": {
            "authoritative_index": "docs/index.md",
            "validator": "python scripts/validate_documentation.py --check",
            "metadata_enforced": True,
            "internal_links_enforced": True,
            "stale_claims_enforced": True,
            "adr_system_enforced": True,
        },
        "documents": rows,
    }


def _count_test_functions(paths: Iterable[Path]) -> tuple[int, Counter[str]]:
    count = 0
    categories: Counter[str] = Counter()
    patterns = {
        "replay_named": re.compile(r"replay", re.IGNORECASE),
        "privacy_or_projection_named": re.compile(
            r"privacy|hidden|projection", re.IGNORECASE
        ),
        "compiler_named": re.compile(r"oracle|compiler|semantic", re.IGNORECASE),
        "server_named": re.compile(r"server|browser|websocket|room", re.IGNORECASE),
        "fuzz_named": re.compile(r"fuzz", re.IGNORECASE),
    }
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test_"
            ):
                count += 1
                for category, pattern in patterns.items():
                    if pattern.search(node.name):
                        categories[category] += 1
    return count, categories


def _regex_test_count(directory: Path, pattern: str) -> int:
    expression = re.compile(pattern, re.MULTILINE)
    return sum(
        len(expression.findall(path.read_text(encoding="utf-8")))
        for path in directory.rglob("*")
        if path.is_file() and path.suffix in {".ts", ".tsx"}
    )


def _discover_test_case_count() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"), pattern="test_*.py")
    if loader.errors:
        raise RuntimeError(
            "Test discovery failed while generating architecture evidence:\n\n"
            + "\n\n".join(loader.errors)
        )
    return suite.countTestCases()


def _test_metrics() -> dict[str, Any]:
    tests = sorted((ROOT / "tests").glob("test_*.py"))
    conventional, categories = _count_test_functions(tests)
    root_text = str(ROOT)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    discovered = _discover_test_case_count()
    rules = _load_json(ROOT / "coverage" / "rules-conformance.json")
    generated_rules = int(rules.get("total_cases") or 0)
    requirements = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").casefold()
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8").casefold()
    mutation_tools = [
        tool for tool in ("mutmut", "cosmic-ray", "mutpy") if tool in requirements + pyproject
    ]
    capability_evidence = _load_json(CAPABILITY_EVIDENCE)
    performance = _load_json(CONTINUOUS_PERFORMANCE_BASELINE)
    return {
        "python": {
            "discovered_total": discovered,
            "conventional_ast_cases": conventional,
            "generated_rule_conformance_cases": generated_rules,
            "reconciles": discovered == conventional + generated_rules,
            "test_files": len(tests),
            "named_cross_cutting_counts": dict(sorted(categories.items())),
        },
        "browser": {
            "playwright_e2e_journeys": _regex_test_count(
                ROOT / "web" / "tests", r"^\s*test\s*\("
            ),
            "node_unit_cases": _regex_test_count(
                ROOT / "web" / "unit", r"^\s*test\s*\("
            ),
        },
        "property_and_fuzz": {
            "hypothesis_configured": "hypothesis" in requirements + pyproject,
            "named_deterministic_fuzz_cases": categories["fuzz_named"],
            "dedicated_property_suite": False,
        },
        "mutation_testing": {
            "configured_tools": mutation_tools,
            "focused_executable_suite": (
                ROOT / "tests" / "test_capability_implementation_mutations.py"
            ).is_file(),
            "capability_mutation_declarations": sum(
                row["evidence_class"] == "mutation"
                for row in capability_evidence["declarations"]
            ),
            "mutation_score": None,
        },
        "performance_benchmarks": {
            "dedicated_suite": (
                ROOT / "tests" / "test_continuous_effect_performance.py"
            ).is_file(),
            "check_script": "scripts/benchmark_continuous_effects.py",
            "baseline": CONTINUOUS_PERFORMANCE_BASELINE.relative_to(
                ROOT
            ).as_posix(),
            "schema_version": performance["schema_version"],
            "source_sha256": performance["source_sha256"],
            "latency_policy": performance["latency_policy"],
            "latency_budget_enforced": False,
            "scenario_count": len(performance["scenarios"]),
            "scenario_names": [
                row["name"] for row in performance["scenarios"]
            ],
        },
    }


def _rules_metrics() -> dict[str, Any]:
    manifest = _load_json(ROOT / "rules" / "manifest.json")
    conformance = _load_json(ROOT / "coverage" / "rules-conformance.json")
    mechanics = _load_json(ROOT / "coverage" / "mechanics-coverage.json")
    return {
        "comprehensive_rules": {
            "effective_date": manifest.get("effective_date"),
            "source_sha256": manifest.get("source_sha256"),
            "rule_count": manifest.get("rule_count"),
            "section_count": manifest.get("section_count"),
            "glossary_count": manifest.get("glossary_count"),
        },
        "conformance": {
            "total_cases": conformance.get("total_cases"),
            "semantic_passing_cases": conformance.get("semantic_passing_cases"),
            "blocked_cases": conformance.get("blocked_cases"),
            "definition_only_cases": conformance.get("definition_only_cases"),
            "unreviewed_cases": conformance.get("unreviewed_cases"),
            "current_snapshot_complete": conformance.get("current_snapshot_complete"),
        },
        "mechanics": {
            "total": mechanics.get("total_mechanics"),
            "trusted": mechanics.get("trusted_mechanics"),
            "status_counts": mechanics.get("status_counts"),
        },
    }


def _coordinates(source: Mapping[str, Any]) -> dict[str, Any]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    historical = _historical_observation(source)
    source_fingerprint = tracked_worktree_source_fingerprint(ROOT)
    return {
        "repository": "MoellerJDev/quorune",
        "default_branch": "main",
        "package_version": project["project"]["version"],
        "requires_python": project["project"]["requires-python"],
        "durable_program_baseline": {
            "main_commit": historical["baseline_main_commit"],
            "worktree_clean": historical["baseline_worktree_clean"],
            "observed_at": historical["observed_at"],
        },
        "evaluated_source_tree": {
            "fingerprint_algorithm": SOURCE_TREE_FINGERPRINT_ALGORITHM,
            "fingerprint": source_fingerprint,
        },
        "historical_audit_observation": {
            "observation_id": historical["observation_id"],
            "phase": historical["phase"],
            "certification": historical["certification"],
        },
        "live_transport_context": {
            "current_feature_head": None,
            "certified_exact_head": None,
            "current_main": None,
            "active_slot_a": None,
            "active_slot_b": None,
            "reason": (
                "Branch, ref, receipt, and worktree-slot facts are intentionally not "
                "persisted in a tracked generated artifact; run `simctl architecture "
                "owners` for live values."
            ),
        },
    }


def build_report() -> dict[str, Any]:
    source, paths, analyses = analyze_production()
    state_dispatch = _state_and_dispatch_metrics(analyses, source)
    semantic_handlers = _semantic_handler_metrics(state_dispatch)
    production = _production_metrics(paths, analyses, source)
    engine = _engine_metrics(analyses, source)
    policy = _load_json(ARCHITECTURE_POLICY)
    baseline = _load_json(GUARD_BASELINE)
    module_classifications = _load_json(MODULE_CLASSIFICATIONS)
    identity_flow = analyze_identity_flows(
        analyses, policy, module_classifications
    )
    if identity_flow["counts"]["prohibited_identity_dispatch_count"]:
        raise ValueError(
            "Generic production code contains prohibited fixed card-identity dispatch"
        )
    exceptions = _load_json(ARCHITECTURE_EXCEPTIONS)
    write_inventory = classify_state_writes(
        state_dispatch["direct_game_state_write_heuristic"]["locations"],
        source=source,
        policy=policy,
        baseline=baseline,
        module_classifications=module_classifications,
    )
    runtime_text = runtime_text_accesses(analyses)
    subsystem_capsules = build_subsystem_capsules(
        root=ROOT,
        source=source,
        analyses=analyses,
        module_classifications=module_classifications,
        writes=write_inventory,
        runtime_text=runtime_text,
        production=production,
        engine=engine,
        exceptions=exceptions,
    )
    return {
        "schema_version": 3,
        "generated": {
            "generator": "scripts/update_architecture_audit.py",
            "source": "platform/architecture-audit-source.json",
            "stale_check": "python scripts/update_architecture_audit.py --check",
            "scope_note": source["program"]["scope_note"],
        },
        "audit": {
            "observation_kind": "historical",
            **_historical_observation(source),
        },
        "coordinates": _coordinates(source),
        "rules": _rules_metrics(),
        "tests": _test_metrics(),
        "architecture": {
            "production": production,
            "engine": engine,
            "imports": _import_metrics(analyses),
            **state_dispatch,
            "direct_game_state_write_ownership": write_inventory,
            "runtime_oracle_text_access": runtime_text,
            "card_identity_flow": identity_flow,
            "semantic_handlers": semantic_handlers,
            "card_named_helpers": source["card_named_helpers"],
            "subsystem_ownership": source["subsystem_ownership"],
            "subsystem_capsules": subsystem_capsules,
            "migration_queue": build_migration_queue(subsystem_capsules),
            "interaction_assurance_baseline": interaction_assurance_summary(ROOT),
            "missing_dedicated_owners": [
                item["id"]
                for item in source["subsystem_ownership"]
                if item["missing_dedicated_owner"]
            ],
            "debt_trend": _debt_trend(
                production, engine, state_dispatch, identity_flow, source
            ),
        },
        "compiler": _compiler_metrics(source, analyses),
        "semantic_packs_and_overrides": _semantic_pack_metrics(source),
        "documentation": _documentation_metrics(source),
    }


def _metadata_lines(
    title: str, authoritative_source: str, audience: str, verified: str
) -> list[str]:
    return [
        "---",
        f'title: "{title}"',
        'status: "generated"',
        f'authoritative_source: "{authoritative_source}"',
        f'verified: "{verified}"',
        f'audience: "{audience}"',
        'maintenance: "generated"',
        "---",
        "",
    ]


def _debt_trend_lines(architecture: Mapping[str, Any]) -> list[str]:
    trend = architecture.get("debt_trend")
    if not trend:
        return []
    lines = [
        "",
        "## Enforced debt trend",
        "",
        f"Baseline: `{trend['baseline_commit']}`. Guard: `{trend['guard']}`.",
        "",
        "| Dimension | Baseline | Current | Delta |",
        "|---|---:|---:|---:|",
    ]
    for name, values in trend["dimensions"].items():
        lines.append(
            f"| `{name}` | {values['baseline']:,} | {values['current']:,} | "
            f"{values['delta']:+,} |"
        )
    return lines


def render_architecture_status(report: Mapping[str, Any]) -> str:
    architecture = report["architecture"]
    production = architecture["production"]
    engine = architecture["engine"]
    write_ownership = architecture["direct_game_state_write_ownership"]
    runtime_text = architecture["runtime_oracle_text_access"]
    interaction_baseline = architecture["interaction_assurance_baseline"]
    docs = report["documentation"]
    tests = report["tests"]
    lines = _metadata_lines(
        "Architecture debt status",
        "coverage/architecture-audit.json",
        "maintainers and rules contributors",
        report["audit"]["baseline_main_commit"],
    )
    lines.extend(
        [
            "# Architecture debt status",
            "",
            "This generated migration dashboard measures the evaluated source tree "
            "against the durable guard baseline and preserves historical observations "
            "as explicitly historical. It does not claim architectural completion, "
            "rules completeness, or universal card support.",
            "",
            "## Baseline coordinates",
            "",
            "- Historical observation (not current head): "
            f"`{report['coordinates']['historical_audit_observation']['observation_id']}`",
            "- Historical baseline main commit: "
            f"`{report['coordinates']['durable_program_baseline']['main_commit']}`",
            "- Evaluated source-tree fingerprint: "
            f"`{report['coordinates']['evaluated_source_tree']['fingerprint']}`",
            f"- Package: `{report['coordinates']['package_version']}`",
            "- Historical CI run: "
            f"[{report['coordinates']['historical_audit_observation']['certification']['run_id']}]"
            f"({report['coordinates']['historical_audit_observation']['certification']['url']}) — "
            f"`{report['coordinates']['historical_audit_observation']['certification']['status']}`",
            "- Live feature/main/certification/Slot A/Slot B coordinates: "
            "`simctl architecture owners`",
            f"- Production scope: {production['file_count']} files, "
            f"{production['physical_lines']:,} physical lines, "
            f"{production['logical_lines']:,} logical lines",
            "",
            "## Central engine debt",
            "",
            f"- `engine.py`: {engine['physical_lines']:,} physical / "
            f"{engine['logical_lines']:,} logical lines",
            f"- Methods: {engine['public_methods']} public, "
            f"{engine['private_methods']} private, {engine['dunder_methods']} dunder",
            f"- Cross-subsystem responsibility groups: "
            f"{engine['cross_subsystem_responsibility_count']}",
            f"- Direct GameState-write heuristic: "
            f"{architecture['direct_game_state_write_heuristic']['count']} locations",
            f"- Semantic-operation branches: "
            f"{architecture['semantic_operation_branches']['count']}",
            f"- Registered typed semantic handlers: "
            f"{architecture['semantic_handlers']['registered_handler_count']} "
            f"across {architecture['semantic_handlers']['registered_operation_count']} "
            "operations",
            f"- Registered typed runtime components: "
            f"{architecture['semantic_handlers']['registered_runtime_handler_count']}",
            f"- Remaining legacy `apply_effect` branches: "
            f"{architecture['semantic_handlers']['legacy_apply_effect_branch_count']}",
            f"- Registered operations still intercepted by engine string dispatch: "
            f"{len(architecture['semantic_handlers']['registered_operations_still_in_legacy_dispatch'])}",
            "- Prohibited fixed card-identity dispatches: "
            f"{architecture['card_identity_flow']['counts']['prohibited_identity_dispatch_count']}",
            "- Card identities are permitted as typed data, compiler binding, display "
            "metadata, generated provenance, reviewed overrides, and exact historical "
            "compatibility; they are prohibited as generic implementation authority.",
            f"- Oracle-ID literals in Python production code: "
            f"{architecture['oracle_id_literals']['count']}",
            "- The Oracle-ID literal ratchet independently protects literal identity "
            "leakage and historical debt; it is not a substitute for behavior-flow "
            "analysis.",
            f"- Card-named helpers: {len(architecture['card_named_helpers'])}",
            f"- Modules above the {production['review_thresholds']['module_logical_lines_review']:,}-logical-line review threshold: "
            f"{production['oversized_module_count']}",
            f"- Functions/methods above the {production['review_thresholds']['function_logical_lines_review']:,}-logical-line review threshold: "
            f"{production['oversized_function_and_method_count']}",
            "- Identity-flow analysis is bounded to declared source and sink shapes "
            "within one module or function; its exact limitations are serialized in "
            "the JSON inventory.",
        ]
    )
    lines.extend(
        [
            "",
            "## Direct GameState-write ownership",
            "",
            f"- Total heuristic locations: {write_ownership['total_detected_writes']}",
            f"- Engine-local locations: {write_ownership['writes_in_commander_engine']}",
            f"- Canonical-owner locations: {write_ownership['writes_in_canonical_owners']}",
            f"- Grandfathered engine debt: {write_ownership['grandfathered_engine_writes']}",
            f"- Unowned locations: {write_ownership['unowned_writes']}",
            f"- New / removed / migrated identities versus the guard baseline: "
            f"{len(write_ownership['newly_added_writes'])} / "
            f"{len(write_ownership['removed_writes'])} / "
            f"{len(write_ownership['migrated_writes'])}",
            "",
            "| Classification | Locations |",
            "|---|---:|",
        ]
    )
    for classification, count in write_ownership["by_classification"].items():
        lines.append(f"| `{classification}` | {count} |")
    lines.extend(
        [
            "",
            "## Runtime Oracle-text access",
            "",
            f"- Total structurally detected accesses: {runtime_text['total_accesses']}",
            "- Prohibited production runtime interpretation: "
            f"{runtime_text['prohibited_runtime_interpretation_count']}",
            "",
            "| Classification | Accesses |",
            "|---|---:|",
        ]
    )
    for classification, count in runtime_text["by_classification"].items():
        lines.append(f"| `{classification}` | {count} |")
    lines.extend(
        [
            "",
            "## Deterministic owner-migration queue",
            "",
            "| Priority | Subsystem | Score | Engine writes | Runtime-text reads |",
            "|---:|---|---:|---:|---:|",
        ]
    )
    for item in architecture["migration_queue"]:
        lines.append(
            f"| {item['priority']} | `{item['subsystem']}` | {item['score']} | "
            f"{item['engine_direct_writes']} | {item['runtime_oracle_text_reads']} |"
        )
    if interaction_baseline["available"]:
        lines.extend(
            [
                "",
                "## Interaction-assurance starting baseline",
                "",
                f"- High-risk pairs: {interaction_baseline['covered_high_risk_pairs']} covered / "
                f"{interaction_baseline['applicable_high_risk_pairs']} applicable "
                f"({interaction_baseline['uncovered_high_risk_pairs']} uncovered)",
                f"- All applicable piece pairs: {interaction_baseline['covered_piece_pairs']} covered / "
                f"{interaction_baseline['applicable_piece_pairs']} applicable",
                f"- Source: `{interaction_baseline['source']}` "
                f"(`{interaction_baseline['source_fingerprint']}`)",
            ]
        )
    lines.extend(_debt_trend_lines(architecture))
    lines.extend(
        [
            "",
            "## Largest production modules",
            "",
            "| File | Language | Physical | Logical |",
            "|---|---:|---:|---:|",
        ]
    )
    for module in production["modules"][:15]:
        lines.append(
            f"| `{module['file']}` | {module['language']} | "
            f"{module['physical_lines']:,} | {module['logical_lines']:,} |"
        )
    lines.extend(
        [
            "",
            "## Largest functions and methods",
            "",
            "| Symbol | File:line | Logical | Physical |",
            "|---|---|---:|---:|",
        ]
    )
    for item in production["largest_functions_and_methods"][:15]:
        lines.append(
            f"| `{item['symbol']}` | `{item['file']}:{item['line']}` | "
            f"{item['logical_lines']} | {item['physical_lines']} |"
        )
    lines.extend(
        [
            "",
            "## Engine responsibility spread",
            "",
            "| Responsibility | Matched methods |",
            "|---|---:|",
        ]
    )
    for item in engine["responsibility_groups"]:
        lines.append(f"| `{item['id']}` | {item['method_count']} |")
    lines.extend(
        [
            "",
            "## Missing dedicated ownership",
            "",
            *(
                f"- `{value}`"
                for value in architecture["missing_dedicated_owners"]
            ),
            "",
            "These are review classifications from the machine-readable source, not "
            "automatic proof that an extraction boundary is correct.",
            "",
            "## Test classes",
            "",
            f"- Python discovered: {tests['python']['discovered_total']:,}",
            f"- Conventional Python cases: "
            f"{tests['python']['conventional_ast_cases']:,}",
            f"- Generated CR conformance cases: "
            f"{tests['python']['generated_rule_conformance_cases']:,}",
            f"- Playwright journeys: {tests['browser']['playwright_e2e_journeys']}",
            f"- Browser unit cases: {tests['browser']['node_unit_cases']}",
            f"- Dedicated property suite: "
            f"{str(tests['property_and_fuzz']['dedicated_property_suite']).lower()}",
            f"- Mutation score: {tests['mutation_testing']['mutation_score']}",
            f"- Focused executable mutation suite: "
            f"{str(tests['mutation_testing']['focused_executable_suite']).lower()}",
            f"- Capability mutation declarations: "
            f"{tests['mutation_testing']['capability_mutation_declarations']}",
            f"- Performance baseline: "
            f"`{tests['performance_benchmarks']['baseline']}` "
            f"({tests['performance_benchmarks']['scenario_count']} scenarios; "
            "latency observational)",
            "",
            "## Documentation drift",
            "",
            f"- Required: {docs['required_count']}",
            f"- Present after generated audit outputs: {docs['present_count']}",
            f"- Missing: {docs['missing_count']}",
            f"- Metadata complete: {docs['metadata_complete_count']}",
            "",
            "The authoritative index, metadata, internal-link, stale-claim, and "
            "ADR policies are enforced by `scripts/validate_documentation.py`. "
            "Detailed document records remain in `coverage/architecture-audit.json`.",
            "",
            "## Regeneration",
            "",
            "```bash",
            "python scripts/update_architecture_audit.py --write",
            "python scripts/update_architecture_audit.py --check",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def render_compiler_status(report: Mapping[str, Any]) -> str:
    compiler = report["compiler"]
    full = compiler["full_oracle"]
    commander = compiler["commander_legal_oracle"]
    capabilities = compiler["rule_capabilities"]
    card_program = compiler["card_program"]
    semantics = report["semantic_packs_and_overrides"]
    lines = _metadata_lines(
        "Compiler coverage status",
        "coverage/architecture-audit.json",
        "compiler and rules contributors",
        report["audit"]["baseline_main_commit"],
    )
    lines.extend(
        [
            "# Compiler coverage status",
            "",
            "This generated report describes only the pinned Oracle corpus and current "
            "compiler. Exact compilation is not the same as complete game-behavior proof.",
            "",
            "## Current representation",
            "",
            f"- Compiler: `{compiler['compiler_version']}`",
            f"- Runtime IR: {compiler['current_ir']}",
            f"- CardProgram V2 present: "
            f"{str(compiler['card_program_v2_present']).lower()}",
            f"- Compiler module: {compiler['compiler_module_physical_lines']:,} physical / "
            f"{compiler['compiler_module_logical_lines']:,} logical lines",
            "",
            "## Canonical CardProgram",
            "",
            f"- Schema version: `{card_program['schema_version']}`",
            f"- Schema: `{card_program['schema']}`",
            f"- Required card fields: {card_program['required_card_fields']}",
            f"- Required per-ability fields: {card_program['required_ability_fields']}",
            f"- Model: `{card_program['model']}`",
            f"- Generated/reviewed adapter: `{card_program['adapter']}`",
            f"- Runtime validator: `{card_program['validator']}`",
            f"- Canonical reviewed registry CardPrograms: "
            f"{card_program['semantic_registry_program_count']}",
            f"- Intrinsic strict-capability-ready CardPrograms: "
            f"{card_program['strict_capability_ready_count']}",
            f"- Trust bases: `{json.dumps(card_program['trust_basis_counts'], sort_keys=True)}`",
            "",
            "## Stages",
            "",
            "| Stage | Current status | Evidence complete |",
            "|---|---|---:|",
        ]
    )
    for stage in compiler["stages"]:
        lines.append(
            f"| `{stage['id']}` | `{stage['current_status']}` | "
            f"{str(stage['all_configured_evidence_present']).lower()} |"
        )
    lines.extend(
        [
            "",
            "## Fine-grained capability registry",
            "",
            f"- Registry schema/version: "
            f"`{capabilities['schema_version']}/{capabilities['registry_version']}`",
            f"- Pinned rules effective date: `{capabilities['effective_date']}`",
            f"- Registry fingerprint: `{capabilities['fingerprint']}`",
            f"- Evidence fingerprint: "
            f"`{capabilities['evidence']['fingerprint']}`",
            f"- Explicit evidence declarations: "
            f"{capabilities['evidence']['declaration_count']}",
            f"- Capability records: {capabilities['total']}",
            f"- Trusted records: "
            f"{capabilities['status_counts'].get('trusted', 0)}",
            f"- Blocked records: "
            f"{capabilities['status_counts'].get('blocked', 0)}",
            f"- Dependency fail-closed statuses: "
            f"`{json.dumps(capabilities['dependency_fail_closed_status_counts'], sort_keys=True)}`",
            f"- Implementation mutation statuses: "
            f"`{json.dumps(capabilities['implementation_mutation_status_counts'], sort_keys=True)}`",
            "",
            "| Broad aggregate | Capability records | Trusted | Blocked members |",
            "|---|---:|---:|---|",
        ]
    )
    for aggregate in capabilities["aggregates"]:
        blocked = ", ".join(
            f"`{value}`" for value in aggregate["blocked_capabilities"]
        ) or "none"
        lines.append(
            f"| `{aggregate['mechanic_id']}` | "
            f"{aggregate['capability_count']} | "
            f"{str(aggregate['trusted']).lower()} | {blocked} |"
        )
    lines.extend(
        [
            "",
            "## Pinned corpus accounting",
            "",
            "| Scope | Oracle IDs | Exact | Partial | Unresolved | Material residuals | Complete |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for label, value in (("Full Oracle", full), ("Commander legal", commander)):
        status = value["status_counts"]
        lines.append(
            f"| {label} | {value['total_oracle_ids']:,} | {status['exact']:,} | "
            f"{status['partial']:,} | {status['unresolved']:,} | "
            f"{value['material_residuals']:,} | "
            f"{str(value['current_snapshot_complete']).lower()} |"
        )
    lines.extend(
        [
            "",
            "## Full-corpus residual kinds",
            "",
            "| Kind | Count |",
            "|---|---:|",
        ]
    )
    for kind, count in sorted(
        full["residual_kinds"].items(), key=lambda item: (-item[1], item[0])
    ):
        lines.append(f"| `{kind}` | {count:,} |")
    lines.extend(
        [
            "",
            "## Semantic packs and implicit overrides",
            "",
            f"- Pack files: {semantics['pack_count']}",
            f"- Program entries: {semantics['program_entries']}",
            f"- Unique program keys: {semantics['unique_program_keys']}",
            f"- Duplicate keys resolved by pack order: {semantics['duplicate_key_count']}",
            f"- Unique Oracle IDs represented: {semantics['unique_oracle_ids']}",
            f"- Card-specific operation names: "
            f"{len(semantics['card_specific_operations'])}",
            f"- Typed card-override boundary present: "
            f"{str(semantics['typed_card_override_boundary_present']).lower()}",
            f"- Explicit typed overrides: {semantics['explicit_typed_override_count']}",
            "",
            "## Snapshot fingerprints",
            "",
            f"- Oracle SHA-256: `{full['snapshot']['oracle_source_sha256']}`",
            f"- Rulings SHA-256: `{full['snapshot']['rulings_source_sha256']}`",
            f"- Oracle updated: `{full['snapshot']['scryfall_oracle_updated_at']}`",
            f"- Rulings updated: `{full['snapshot']['scryfall_rulings_updated_at']}`",
            "",
            "## Boundary",
            "",
            "The current compiler is partial and interleaved. Full-corpus exactness is "
            "not claimed. Fine-grained closure spans the registered typed capabilities for "
            "represented damage, draw, continuous-effect, attachment, mana, cast-timing, and "
            "combat families; unregistered or blocked dependencies remain residual. CardProgram "
            "V2 provides canonical aggregation, validation, and replay pinning, while broader "
            "typed handlers and fully distinct compiler stages remain incremental work.",
            "",
        ]
    )
    return "\n".join(lines)


def _machine_report_fingerprint(report: Mapping[str, Any]) -> str:
    return hashlib.sha256(_serialize_json(report).encode("utf-8")).hexdigest()


def render_compact_architecture_status(report: Mapping[str, Any]) -> str:
    architecture = report["architecture"]
    engine = architecture["engine"]
    production = architecture["production"]
    fingerprint = _machine_report_fingerprint(report)
    command = (
        r".\.venv\Scripts\python.exe scripts\update_architecture_audit.py "
        r"--write"
    )
    blockers = [
        *(f"Missing dedicated owner: `{owner}`." for owner in architecture["missing_dedicated_owners"]),
    ]
    intercepted = architecture["semantic_handlers"][
        "registered_operations_still_in_legacy_dispatch"
    ]
    if intercepted:
        blockers.append(
            "Registered operations remain in legacy dispatch: "
            + ", ".join(f"`{item}`" for item in intercepted[:5])
            + "."
        )
    if not blockers:
        blockers.append("None detected by the configured architecture policy.")
    lines = [
        "---",
        'title: "Architecture debt status"',
        'status: "generated"',
        'authoritative_source: "coverage/architecture-audit.json"',
        f'verified: "{fingerprint}"',
        'audience: "maintainers and rules contributors"',
        'maintenance: "generated"',
        'generated_source: "coverage/architecture-audit.json"',
        f'generation_command: "{command}"',
        "---",
        "",
        "# Architecture debt status",
        "",
        f"Source fingerprint: `{fingerprint}`",
        "",
        "## Current top-level state",
        "",
        f"- Production logical lines: `{production['logical_lines']}`",
        f"- Engine logical lines: `{engine['logical_lines']}`",
        "- Direct GameState-write heuristic: "
        f"`{architecture['direct_game_state_write_heuristic']['count']}`",
        "- Registered typed semantic handlers: "
        f"`{architecture['semantic_handlers']['registered_handler_count']}`",
        "- Registered runtime components: "
        f"`{architecture['semantic_handlers']['registered_runtime_handler_count']}`",
        f"- Oversized production modules: `{production['oversized_module_count']}`",
        "",
        "## Top blockers",
        "",
        *(f"- {item}" for item in blockers[:5]),
        "",
        "Complete module, symbol, ownership, test, and documentation inventories are in "
        "the [machine-readable architecture audit](../coverage/architecture-audit.json).",
        "",
        "Exact generation command:",
        "",
        "```powershell",
        command,
        "```",
        "",
    ]
    return "\n".join(lines)


def render_compact_compiler_status(report: Mapping[str, Any]) -> str:
    compiler = report["compiler"]
    commander = compiler["commander_legal_oracle"]
    capabilities = compiler["rule_capabilities"]
    target_effect_assurance = compiler["target_effect_corpus_assurance"][
        "commander"
    ]
    fingerprint = _machine_report_fingerprint(report)
    command = (
        r".\.venv\Scripts\python.exe scripts\update_architecture_audit.py "
        r"--write"
    )
    blockers: list[str] = []
    if not commander["current_snapshot_complete"]:
        blockers.append("The pinned Commander Oracle snapshot is not capability-complete.")
    if commander["material_residuals"]:
        blockers.append(
            f"Material compiler residuals remain: `{commander['material_residuals']}`."
        )
    blocked_capabilities = capabilities["status_counts"].get("blocked", 0)
    if blocked_capabilities:
        blockers.append(f"Blocked capability records remain: `{blocked_capabilities}`.")
    incomplete_stages = [
        stage["id"]
        for stage in compiler["stages"]
        if not stage["all_configured_evidence_present"]
    ]
    if incomplete_stages:
        blockers.append(
            "Configured evidence is incomplete for: "
            + ", ".join(f"`{stage}`" for stage in incomplete_stages[:5])
            + "."
        )
    if not blockers:
        blockers.append("None detected by the configured compiler audit.")
    lines = [
        "---",
        'title: "Compiler coverage status"',
        'status: "generated"',
        'authoritative_source: "coverage/architecture-audit.json"',
        f'verified: "{fingerprint}"',
        'audience: "compiler and rules contributors"',
        'maintenance: "generated"',
        'generated_source: "coverage/architecture-audit.json"',
        f'generation_command: "{command}"',
        "---",
        "",
        "# Compiler coverage status",
        "",
        f"Source fingerprint: `{fingerprint}`",
        "",
        "## Current top-level state",
        "",
        f"- Compiler version: `{compiler['compiler_version']}`",
        f"- Runtime IR: `{compiler['current_ir']}`",
        f"- CardProgram schema version: `{compiler['card_program']['schema_version']}`",
        f"- Commander Oracle objects: `{commander['total_oracle_ids']}`",
        f"- Exact fraction: `{commander['exact_fraction']}`",
        f"- Capability records: `{capabilities['total']}`",
        (
            "- Assured fixed-target compiler nodes/shapes: "
            f"`{target_effect_assurance['total_nodes']}` / "
            f"`{target_effect_assurance['shape_count']}`"
        ),
        "",
        "## Top blockers",
        "",
        *(f"- {item}" for item in blockers[:5]),
        "",
        "Complete corpus, residual, stage, capability, and CardProgram inventories are "
        "in the [machine-readable architecture audit](../coverage/architecture-audit.json). "
        "The corpus-derived fixed-target grammar shapes and representative identities are "
        "in the [Commander Oracle census](../coverage/oracle-coverage-commander.json).",
        "",
        "Exact generation command:",
        "",
        "```powershell",
        command,
        "```",
        "",
    ]
    return "\n".join(lines)


def _outputs(report: Mapping[str, Any]) -> dict[Path, str]:
    return {
        JSON_OUTPUT: _serialize_json(report),
        ARCHITECTURE_STATUS: render_compact_architecture_status(report),
        COMPILER_STATUS: render_compact_compiler_status(report),
    }


def _write_outputs(report: Mapping[str, Any]) -> None:
    for path, content in _outputs(report).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def _check_outputs(report: Mapping[str, Any]) -> list[str]:
    stale = []
    for path, expected in _outputs(report).items():
        actual = path.read_text(encoding="utf-8") if path.is_file() else None
        if actual != expected:
            stale.append(path.relative_to(ROOT).as_posix())
    return stale


def _source_readiness_errors(root: Path) -> list[str]:
    """Reject a source checkpoint before scheduling its broad matrices."""
    path = root / "coverage/architecture-audit.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        observed = report["coordinates"]["evaluated_source_tree"]
    except (OSError, ValueError, KeyError, TypeError):
        return ["Architecture source receipt is missing or malformed"]
    if not isinstance(observed, Mapping):
        return ["Architecture source receipt is missing or malformed"]
    if observed.get("fingerprint_algorithm") != SOURCE_TREE_FINGERPRINT_ALGORITHM:
        return ["Architecture source fingerprint algorithm is unsupported"]
    if observed.get("fingerprint") != tracked_worktree_source_fingerprint(root):
        return ["Architecture source receipt is stale; install current generated outputs"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--check-source-readiness", action="store_true")
    args = parser.parse_args()
    if args.check_source_readiness:
        errors = _source_readiness_errors(ROOT)
        if errors:
            print("; ".join(errors), file=sys.stderr)
            return 1
        print(json.dumps({"ok": True, "source_readiness_only": True}))
        return 0
    report = build_report()
    if args.write:
        _write_outputs(report)
        print(
            json.dumps(
                {
                    "ok": True,
                    "outputs": [
                        *(
                            path.relative_to(ROOT).as_posix()
                            for path in _outputs(report)
                        ),
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    stale = _check_outputs(report)
    if stale:
        print(
            "architecture audit is stale; run `python "
            "scripts/update_architecture_audit.py --write`: "
            + ", ".join(stale),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"ok": True, "stale_outputs": []}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"architecture audit failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
