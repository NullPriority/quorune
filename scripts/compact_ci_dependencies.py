from __future__ import annotations

import ast
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable, Mapping

from quorune.util import normalize_card_name
from scripts.source_syntax import SourceFiles, SyntaxSnapshot


MANIFEST_SCHEMA_VERSION = 2
MANIFEST_KIND = "compact_ci_card_database_inputs"


class CompactCIDependencyError(ValueError):
    """The compact CI dependency model is malformed or not closed."""


@dataclass(frozen=True, order=True)
class Provenance:
    module: str
    source: str
    symbol: str
    line: int

    def to_dict(self) -> dict[str, object]:
        return {
            "module": self.module,
            "source": self.source,
            "symbol": self.symbol,
            "line": self.line,
        }


@dataclass(frozen=True, order=True)
class Requirement:
    kind: str
    value: str
    discovery: str
    provenance: Provenance

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "value": self.value,
            "discovery": self.discovery,
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True, order=True)
class DynamicSite:
    module: str
    source: str
    symbol: str
    line: int
    kind: str

    def to_dict(self) -> dict[str, object]:
        return {
            "module": self.module,
            "source": self.source,
            "symbol": self.symbol,
            "line": self.line,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class WrapperSpec:
    name: str
    parameter: str
    position: int
    kind: str
    default_values: tuple[str, ...] = ()


def _canonical_relative_path(value: str, *, field: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or PureWindowsPath(value).is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
    ):
        raise CompactCIDependencyError(
            f"{field} must be a canonical repository-relative POSIX path: {value}"
        )
    return value


def _strict_strings(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise CompactCIDependencyError(
            f"{field} must be a list of nonempty strings"
        )
    if value != sorted(set(value)):
        raise CompactCIDependencyError(
            f"{field} must be sorted and contain no duplicates"
        )
    return tuple(value)


def load_dependency_manifest(
    path: Path,
    *,
    root: Path,
) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompactCIDependencyError(
            f"Compact CI fixture manifest cannot be read: {path}"
        ) from exc
    expected = {
        "schema_version",
        "fixture_kind",
        "fixtures",
        "dynamic_requirements",
        "full_database_only",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise CompactCIDependencyError(
            "Compact CI fixture manifest has unknown or missing fields"
        )
    if value["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise CompactCIDependencyError(
            "Unsupported compact CI fixture manifest schema"
        )
    if value["fixture_kind"] != MANIFEST_KIND:
        raise CompactCIDependencyError("Unexpected compact CI fixture manifest kind")

    fixtures = _strict_strings(value["fixtures"], field="fixtures")
    if not fixtures:
        raise CompactCIDependencyError("fixtures cannot be empty")
    resolved_fixtures: list[str] = []
    root_resolved = root.resolve()
    for entry in fixtures:
        _canonical_relative_path(entry, field="fixtures")
        fixture = (root / entry).resolve(strict=False)
        try:
            fixture.relative_to(root_resolved)
        except ValueError as exc:
            raise CompactCIDependencyError(
                f"Compact CI fixture resolves outside the repository: {entry}"
            ) from exc
        if not fixture.is_file():
            raise CompactCIDependencyError(
                f"Compact CI fixture does not exist: {entry}"
            )
        resolved_fixtures.append(entry)

    declarations = value["dynamic_requirements"]
    if not isinstance(declarations, list):
        raise CompactCIDependencyError("dynamic_requirements must be a list")
    normalized_declarations: list[dict[str, object]] = []
    seen_declarations: set[tuple[str, str]] = set()
    declaration_fields = {
        "source",
        "symbol",
        "card_names",
        "oracle_ids",
        "deck_files",
        "fixture_files",
        "rationale",
    }
    for index, declaration in enumerate(declarations):
        if not isinstance(declaration, dict) or set(declaration) != declaration_fields:
            raise CompactCIDependencyError(
                f"dynamic_requirements[{index}] has an invalid shape"
            )
        source = _canonical_relative_path(
            str(declaration["source"]), field=f"dynamic_requirements[{index}].source"
        )
        symbol = declaration["symbol"]
        rationale = declaration["rationale"]
        if not isinstance(symbol, str) or not symbol:
            raise CompactCIDependencyError(
                f"dynamic_requirements[{index}].symbol must be nonempty"
            )
        if not isinstance(rationale, str) or not rationale.strip():
            raise CompactCIDependencyError(
                f"dynamic_requirements[{index}].rationale must be nonempty"
            )
        identity = (source, symbol)
        if identity in seen_declarations:
            raise CompactCIDependencyError(
                f"Duplicate dynamic requirement declaration: {source}::{symbol}"
            )
        seen_declarations.add(identity)
        card_names = _strict_strings(
            declaration["card_names"],
            field=f"dynamic_requirements[{index}].card_names",
        )
        oracle_ids = _strict_strings(
            declaration["oracle_ids"],
            field=f"dynamic_requirements[{index}].oracle_ids",
        )
        deck_files = _strict_strings(
            declaration["deck_files"],
            field=f"dynamic_requirements[{index}].deck_files",
        )
        fixture_files = _strict_strings(
            declaration["fixture_files"],
            field=f"dynamic_requirements[{index}].fixture_files",
        )
        for entry in (*deck_files, *fixture_files):
            _canonical_relative_path(
                entry, field=f"dynamic_requirements[{index}] path"
            )
        if not any((card_names, oracle_ids, deck_files, fixture_files)):
            raise CompactCIDependencyError(
                f"dynamic_requirements[{index}] declares no requirement"
            )
        normalized_declarations.append(
            {
                "source": source,
                "symbol": symbol,
                "card_names": card_names,
                "oracle_ids": oracle_ids,
                "deck_files": deck_files,
                "fixture_files": fixture_files,
                "rationale": rationale.strip(),
            }
        )

    exclusions = value["full_database_only"]
    if not isinstance(exclusions, list):
        raise CompactCIDependencyError("full_database_only must be a list")
    normalized_exclusions: list[dict[str, str]] = []
    seen_modules: set[str] = set()
    for index, exclusion in enumerate(exclusions):
        if not isinstance(exclusion, dict) or set(exclusion) != {"module", "reason"}:
            raise CompactCIDependencyError(
                f"full_database_only[{index}] has an invalid shape"
            )
        module = exclusion["module"]
        reason = exclusion["reason"]
        if (
            not isinstance(module, str)
            or not module.startswith("test_")
            or "." in module
            or module in seen_modules
        ):
            raise CompactCIDependencyError(
                f"full_database_only[{index}].module is invalid or duplicated"
            )
        if not isinstance(reason, str) or not reason.strip():
            raise CompactCIDependencyError(
                f"full_database_only[{index}].reason must be nonempty"
            )
        seen_modules.add(module)
        normalized_exclusions.append({"module": module, "reason": reason.strip()})

    return {
        "fixtures": tuple(resolved_fixtures),
        "dynamic_requirements": tuple(normalized_declarations),
        "full_database_only": tuple(normalized_exclusions),
    }


def fixture_paths(
    manifest_path: Path,
    *,
    root: Path,
) -> tuple[Path, ...]:
    manifest = load_dependency_manifest(manifest_path, root=root)
    return tuple(root / relative for relative in manifest["fixtures"])


def _aliases(card: Mapping[str, object]) -> tuple[str, ...]:
    aliases = {str(card.get("name") or "")}
    name = str(card.get("name") or "")
    if " // " in name:
        aliases.update(name.split(" // "))
    faces = card.get("card_faces") or []
    if isinstance(faces, list):
        for face in faces:
            if isinstance(face, dict) and face.get("name"):
                aliases.add(str(face["name"]))
    return tuple(sorted(alias for alias in aliases if alias))


def index_fixture_ownership(
    paths: Iterable[Path],
    *,
    root: Path,
) -> dict[str, object]:
    by_oracle: dict[str, dict[str, object]] = {}
    by_name: dict[str, str] = {}
    conflicts: list[dict[str, object]] = []
    ruling_count = 0
    for path in paths:
        relative = path.relative_to(root).as_posix()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise CompactCIDependencyError(
                f"Unsupported public card fixture schema: {relative}"
            )
        cards = payload.get("cards", [])
        rulings = payload.get("rulings", [])
        if not isinstance(cards, list) or not isinstance(rulings, list):
            raise CompactCIDependencyError(
                f"Fixture cards and rulings must be lists: {relative}"
            )
        ruling_count += len(rulings)
        for card in cards:
            if not isinstance(card, dict):
                raise CompactCIDependencyError(
                    f"Fixture card entry must be an object: {relative}"
                )
            oracle_id = str(card.get("oracle_id") or "")
            name = str(card.get("name") or "")
            if not oracle_id or not name:
                raise CompactCIDependencyError(
                    f"Card fixture entry is missing identity: {relative}"
                )
            normalized = normalize_card_name(name)
            existing = by_oracle.get(oracle_id)
            if existing is not None and existing["payload"] != card:
                conflicts.append(
                    {
                        "kind": "oracle_id",
                        "identity": oracle_id,
                        "fixtures": sorted({*existing["owners"], relative}),
                    }
                )
                continue
            named_oracle = by_name.get(normalized)
            if named_oracle is not None and named_oracle != oracle_id:
                conflicts.append(
                    {
                        "kind": "normalized_name",
                        "identity": normalized,
                        "oracle_ids": sorted({named_oracle, oracle_id}),
                    }
                )
                continue
            if existing is None:
                existing = {
                    "oracle_id": oracle_id,
                    "canonical_name": name,
                    "normalized_name": normalized,
                    "aliases": set(),
                    "owners": set(),
                    "payload": card,
                }
                by_oracle[oracle_id] = existing
            existing["owners"].add(relative)
            for alias in _aliases(card):
                normalized_alias = normalize_card_name(alias)
                alias_oracle = by_name.get(normalized_alias)
                if alias_oracle is not None and alias_oracle != oracle_id:
                    conflicts.append(
                        {
                            "kind": "alias",
                            "identity": normalized_alias,
                            "oracle_ids": sorted({alias_oracle, oracle_id}),
                        }
                    )
                    continue
                by_name[normalized_alias] = oracle_id
                existing["aliases"].add(alias)

    ownership = [
        {
            "oracle_id": row["oracle_id"],
            "canonical_name": row["canonical_name"],
            "normalized_name": row["normalized_name"],
            "aliases": sorted(row["aliases"]),
            "fixture_owners": sorted(row["owners"]),
            "provided_identically_by_multiple_fixtures": len(row["owners"]) > 1,
        }
        for row in sorted(by_oracle.values(), key=lambda item: item["oracle_id"])
    ]
    return {
        "cards": ownership,
        "card_count": len(ownership),
        "ruling_count": ruling_count,
        "conflicts": sorted(
            conflicts,
            key=lambda row: (str(row["kind"]), str(row["identity"])),
        ),
        "oracle_by_normalized_name": by_name,
        "owners_by_oracle": {
            oracle_id: sorted(row["owners"])
            for oracle_id, row in sorted(by_oracle.items())
        },
    }


def _expression_values(
    node: ast.AST | None,
    environment: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...] | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, ast.Name):
        return environment.get(node.id)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values: set[str] = set()
        for element in node.elts:
            resolved = _expression_values(element, environment)
            if resolved is None:
                return None
            values.update(resolved)
        return tuple(sorted(values))
    if isinstance(node, ast.Dict):
        values: set[str] = set()
        for element in node.values:
            resolved = _expression_values(element, environment)
            if resolved is None:
                return None
            values.update(resolved)
        return tuple(sorted(values))
    if isinstance(node, ast.IfExp):
        left = _expression_values(node.body, environment)
        right = _expression_values(node.orelse, environment)
        if left is None or right is None:
            return None
        return tuple(sorted({*left, *right}))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _expression_values(node.left, environment)
        right = _expression_values(node.right, environment)
        if left is None or right is None:
            return None
        return tuple(
            sorted(
                {
                    (Path(prefix) / suffix).as_posix()
                    for prefix in left
                    for suffix in right
                }
            )
        )
    if isinstance(node, ast.Call):
        name = _call_name(node.func)
        if name in {"Path", "str"} and len(node.args) == 1:
            return _expression_values(node.args[0], environment)
        if isinstance(node.func, ast.Attribute) and node.func.attr in {
            "resolve",
            "as_posix",
        }:
            return _expression_values(node.func.value, environment)
    return None


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _source_symbol(
    node: ast.AST,
    functions: tuple[tuple[int, int, str], ...],
) -> str:
    line = int(getattr(node, "lineno", 0))
    candidates = [
        (end - start, symbol)
        for start, end, symbol in functions
        if start <= line <= end
    ]
    return min(candidates, default=(0, "<module>"))[1]


def _function_inventory(tree: ast.AST) -> tuple[tuple[int, int, str], ...]:
    inventory: list[tuple[int, int, str]] = []

    def visit(node: ast.AST, parents: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, (*parents, child.name))
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbol = ".".join((*parents, child.name))
                inventory.append(
                    (
                        int(child.lineno),
                        int(getattr(child, "end_lineno", child.lineno)),
                        symbol,
                    )
                )
                visit(child, (*parents, child.name))
            else:
                visit(child, parents)

    visit(tree, ())
    return tuple(sorted(inventory))


class DependencySourceScope:
    """Share only syntax facts; requirement and binding contexts stay per module."""

    def __init__(self) -> None:
        self.files = SourceFiles()
        self._functions: dict[ast.AST, tuple[tuple[int, int, str], ...]] = {}

    def functions(self, tree: ast.AST) -> tuple[tuple[int, int, str], ...]:
        if tree not in self._functions:
            self._functions[tree] = _function_inventory(tree)
        return self._functions[tree]


def _environments(
    tree: ast.AST,
    *,
    root: Path,
    syntax: SyntaxSnapshot | None = None,
    functions: tuple[tuple[int, int, str], ...] | None = None,
) -> dict[str, dict[str, tuple[str, ...]]]:
    base = {
        "ROOT": (root.as_posix(),),
        "TESTS": ((root / "tests").as_posix(),),
    }
    result: dict[str, dict[str, tuple[str, ...]]] = {"<module>": dict(base)}
    functions = _function_inventory(tree) if functions is None else functions
    walk = ast.walk if syntax is None else syntax.walk
    nodes_by_symbol: dict[str, ast.AST] = {}
    for node in walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbol = _source_symbol(node, functions)
            nodes_by_symbol[symbol] = node
    for symbol, node in nodes_by_symbol.items():
        environment = dict(base)
        for candidate in walk(node):
            if isinstance(candidate, (ast.Assign, ast.AnnAssign)):
                targets = (
                    candidate.targets
                    if isinstance(candidate, ast.Assign)
                    else [candidate.target]
                )
                values = _expression_values(candidate.value, environment)
                if values is not None:
                    for target in targets:
                        if isinstance(target, ast.Name):
                            environment[target.id] = values
            elif isinstance(candidate, ast.For) and isinstance(
                candidate.target, ast.Name
            ):
                values = _expression_values(candidate.iter, environment)
                if values is not None:
                    environment[candidate.target.id] = values
            elif (
                isinstance(candidate, ast.For)
                and isinstance(candidate.target, (ast.Tuple, ast.List))
                and isinstance(candidate.iter, (ast.Tuple, ast.List))
            ):
                columns: list[set[str]] = [
                    set() for _ in candidate.target.elts
                ]
                valid = True
                for row in candidate.iter.elts:
                    if (
                        not isinstance(row, (ast.Tuple, ast.List))
                        or len(row.elts) != len(columns)
                    ):
                        valid = False
                        break
                    for index, cell in enumerate(row.elts):
                        values = _expression_values(cell, environment)
                        if values is None:
                            valid = False
                            break
                        columns[index].update(values)
                if valid:
                    for target, values in zip(
                        candidate.target.elts, columns, strict=True
                    ):
                        if isinstance(target, ast.Name):
                            environment[target.id] = tuple(sorted(values))
        result[symbol] = environment
    return result


def _assignment_mode(value: ast.AST, *, has_local_builder: bool) -> str | None:
    if isinstance(value, ast.Call):
        name = _call_name(value.func)
        if name == "load_assets":
            return "compact"
        if name == "CardDatabase":
            if value.args and isinstance(value.args[0], ast.Name):
                return "compact" if value.args[0].id == "DB_PATH" else "local"
            return "local"
        if any(marker in name.casefold() for marker in ("focused", "fixture")):
            return "local"
    if (
        has_local_builder
        and isinstance(value, ast.Attribute)
        and value.attr in {"db", "database"}
    ):
        return "local"
    return None


def _database_modes(
    tree: ast.AST,
    *,
    syntax: SyntaxSnapshot | None = None,
    functions: tuple[tuple[int, int, str], ...] | None = None,
) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    functions = _function_inventory(tree) if functions is None else functions
    walk = ast.walk if syntax is None else syntax.walk
    has_local_builder = any(
        isinstance(node, ast.Call) and _call_name(node.func) == "build_fixture_database"
        for node in walk(tree)
    )
    class_modes: dict[str, str] = {}
    name_modes: dict[tuple[str, str], str] = {}
    for node in walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        symbol = _source_symbol(node, functions)
        class_name = symbol.split(".", 1)[0] if "." in symbol else ""
        if isinstance(value, ast.Call) and _call_name(value.func) == "load_assets":
            for target in targets:
                if isinstance(target, (ast.Tuple, ast.List)) and target.elts:
                    first = target.elts[0]
                    if isinstance(first, ast.Name):
                        name_modes[(symbol, first.id)] = "compact"
                    elif isinstance(first, ast.Attribute) and class_name:
                        class_modes[class_name] = "compact"
            continue
        mode = _assignment_mode(value, has_local_builder=has_local_builder)
        if mode is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                name_modes[(symbol, target.id)] = mode
            elif (
                isinstance(target, ast.Attribute)
                and target.attr in {"db", "database"}
                and class_name
            ):
                class_modes[class_name] = mode
    return class_modes, name_modes


def _receiver_mode(
    receiver: ast.AST,
    *,
    symbol: str,
    class_modes: Mapping[str, str],
    name_modes: Mapping[tuple[str, str], str],
) -> str | None:
    if isinstance(receiver, ast.Name):
        return name_modes.get((symbol, receiver.id))
    if isinstance(receiver, ast.Attribute) and receiver.attr in {"db", "database"}:
        class_name = symbol.split(".", 1)[0] if "." in symbol else ""
        return class_modes.get(class_name)
    return None


def _wrapper_inventory(
    tree: ast.AST,
    *,
    class_modes: Mapping[str, str],
    name_modes: Mapping[tuple[str, str], str],
    syntax: SyntaxSnapshot | None = None,
    functions: tuple[tuple[int, int, str], ...] | None = None,
) -> tuple[WrapperSpec, ...]:
    wrappers: set[WrapperSpec] = set()
    functions = _function_inventory(tree) if functions is None else functions
    walk = ast.walk if syntax is None else syntax.walk
    for node in walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        symbol = _source_symbol(node, functions)
        positional = [*node.args.posonlyargs, *node.args.args]
        offset = 1 if positional and positional[0].arg in {"self", "cls"} else 0
        parameter_positions = {
            parameter.arg: index - offset
            for index, parameter in enumerate(positional)
            if index >= offset
        }
        parameter_positions.update(
            {parameter.arg: -1 for parameter in node.args.kwonlyargs}
        )
        defaults: dict[str, tuple[str, ...]] = {}
        positional_defaults = [None] * (
            len(positional) - len(node.args.defaults)
        ) + list(node.args.defaults)
        for parameter, default in zip(
            positional, positional_defaults, strict=True
        ):
            resolved = _expression_values(default, {})
            if resolved is not None:
                defaults[parameter.arg] = resolved
        for parameter, default in zip(
            node.args.kwonlyargs, node.args.kw_defaults, strict=True
        ):
            resolved = _expression_values(default, {})
            if resolved is not None:
                defaults[parameter.arg] = resolved
        for candidate in walk(node):
            if not isinstance(candidate, ast.Call) or not candidate.args:
                continue
            kind = {
                "lookup": "card_name",
                "by_oracle_id": "oracle_id",
            }.get(_call_name(candidate.func))
            receiver = (
                candidate.func.value
                if isinstance(candidate.func, ast.Attribute)
                else None
            )
            argument = candidate.args[0]
            if (
                kind is None
                or receiver is None
                or _receiver_mode(
                    receiver,
                    symbol=symbol,
                    class_modes=class_modes,
                    name_modes=name_modes,
                )
                == "local"
                or not isinstance(argument, ast.Name)
                or argument.id not in parameter_positions
            ):
                continue
            wrappers.add(
                WrapperSpec(
                    name=node.name,
                    parameter=argument.id,
                    position=parameter_positions[argument.id],
                    kind=kind,
                    default_values=defaults.get(argument.id, ()),
                )
            )
    return tuple(sorted(wrappers, key=lambda row: (row.name, row.kind, row.position)))


def _local_support_paths(
    test_path: Path, *, root: Path, sources: DependencySourceScope | None = None,
) -> tuple[Path, ...]:
    tests_root = root / "tests"
    visited: set[Path] = set()
    sources = DependencySourceScope() if sources is None else sources

    def visit(path: Path) -> None:
        resolved = path.resolve()
        if resolved in visited or not path.is_file():
            return
        visited.add(resolved)
        parsed = sources.files.read(path)
        for node in parsed.syntax.walk():
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                leaf = module.removeprefix("tests.").split(".")[0]
                candidate = tests_root / f"{leaf}.py"
                if candidate.is_file():
                    visit(candidate)

    visit(test_path)
    return tuple(sorted((Path(path) for path in visited), key=lambda path: path.as_posix()))


def _receiver_is_deck_loader(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return "loader" in node.id.casefold()
    if isinstance(node, ast.Attribute):
        return "loader" in node.attr.casefold()
    if isinstance(node, ast.Call):
        return _call_name(node.func) == "DeckLoader"
    return False


def _relative_requirement_path(value: str, *, root: Path) -> str | None:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    try:
        return path.resolve(strict=False).relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def discover_module_requirements(
    module: str,
    *,
    root: Path,
    sources: DependencySourceScope | None = None,
) -> tuple[tuple[Requirement, ...], tuple[DynamicSite, ...], tuple[str, ...]]:
    test_path = root / "tests" / f"{module}.py"
    if not test_path.is_file():
        return (), (), (f"Missing test source: tests/{module}.py",)
    try:
        sources = DependencySourceScope() if sources is None else sources
        paths = _local_support_paths(test_path, root=root, sources=sources)
        parsed = {
            path: sources.files.read(path)
            for path in paths
        }
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return (), (), (f"Cannot parse {test_path.relative_to(root)}: {exc}",)
    modes_by_path = {
        path: _database_modes(value.tree, syntax=value.syntax,
            functions=sources.functions(value.tree))
        for path, value in parsed.items()
    }
    combined_class_modes = {
        name: mode
        for class_modes, _ in modes_by_path.values()
        for name, mode in class_modes.items()
    }
    changed = True
    while changed:
        changed = False
        for value in parsed.values():
            for node in value.syntax.walk():
                if not isinstance(node, ast.ClassDef):
                    continue
                inherited = next(
                    (
                        combined_class_modes[_call_name(base)]
                        for base in node.bases
                        if _call_name(base) in combined_class_modes
                    ),
                    None,
                )
                if inherited is not None and node.name not in combined_class_modes:
                    combined_class_modes[node.name] = inherited
                    changed = True
    wrappers = tuple(
        wrapper
        for path, value in parsed.items()
        for wrapper in _wrapper_inventory(
            value.tree,
            class_modes={**combined_class_modes, **modes_by_path[path][0]},
            name_modes=modes_by_path[path][1],
            syntax=value.syntax,
            functions=sources.functions(value.tree),
        )
    )
    wrappers_by_name: dict[str, list[WrapperSpec]] = {}
    for wrapper in wrappers:
        wrappers_by_name.setdefault(wrapper.name, []).append(wrapper)

    requirements: set[Requirement] = set()
    dynamic: set[DynamicSite] = set()
    for path, value in parsed.items():
        tree = value.tree
        walk = value.syntax.walk
        relative = path.relative_to(root).as_posix()
        functions = sources.functions(tree)
        environments = _environments(tree, root=root, syntax=value.syntax, functions=functions)
        local_class_modes, name_modes = modes_by_path[path]
        class_modes = {**combined_class_modes, **local_class_modes}
        function_parameters: dict[str, set[str]] = {}
        for node in walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbol = _source_symbol(node, functions)
                function_parameters[symbol] = {
                    argument.arg
                    for argument in (
                        *node.args.posonlyargs,
                        *node.args.args,
                        *node.args.kwonlyargs,
                    )
                }
        for call in (node for node in walk(tree) if isinstance(node, ast.Call)):
            symbol = _source_symbol(call, functions)
            environment = environments.get(symbol, environments["<module>"])
            provenance = Provenance(module, relative, symbol, int(call.lineno))
            call_name = _call_name(call.func)
            direct_kind = {
                "lookup": "card_name",
                "by_oracle_id": "oracle_id",
            }.get(call_name)
            if direct_kind is not None and call.args:
                receiver = (
                    call.func.value
                    if isinstance(call.func, ast.Attribute)
                    else None
                )
                if receiver is not None and _receiver_mode(
                    receiver,
                    symbol=symbol,
                    class_modes=class_modes,
                    name_modes=name_modes,
                ) == "local":
                    continue
                argument = call.args[0]
                if (
                    isinstance(argument, ast.Name)
                    and argument.id in function_parameters.get(symbol, set())
                ):
                    continue
                if isinstance(argument, ast.Attribute) and argument.attr in {
                    "oracle_id",
                    "printed_name",
                }:
                    continue
                values = _expression_values(argument, environment)
                if values is None:
                    dynamic.add(
                        DynamicSite(module, relative, symbol, int(call.lineno), direct_kind)
                    )
                else:
                    for value in values:
                        requirements.add(
                            Requirement(direct_kind, value, "static", provenance)
                        )

            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "load"
                and _receiver_is_deck_loader(call.func.value)
                and call.args
            ):
                class_name = symbol.split(".", 1)[0] if "." in symbol else ""
                if class_modes.get(class_name) == "local":
                    continue
                values = _expression_values(call.args[0], environment)
                if values is None:
                    dynamic.add(
                        DynamicSite(module, relative, symbol, int(call.lineno), "deck_file")
                    )
                else:
                    for value in values:
                        relative_path = _relative_requirement_path(value, root=root)
                        if relative_path is None:
                            dynamic.add(
                                DynamicSite(
                                    module,
                                    relative,
                                    symbol,
                                    int(call.lineno),
                                    "deck_file",
                                )
                            )
                        else:
                            requirements.add(
                                Requirement(
                                    "deck_file",
                                    relative_path,
                                    "static",
                                    provenance,
                                )
                            )

            for wrapper in wrappers_by_name.get(call_name, ()):
                argument: ast.AST | None = None
                if 0 <= wrapper.position < len(call.args):
                    argument = call.args[wrapper.position]
                else:
                    argument = next(
                        (
                            keyword.value
                            for keyword in call.keywords
                            if keyword.arg == wrapper.parameter
                        ),
                        None,
                    )
                values = (
                    wrapper.default_values
                    if argument is None and wrapper.default_values
                    else _expression_values(argument, environment)
                )
                if argument is None and values is None:
                    continue
                if values is None:
                    dynamic.add(
                        DynamicSite(
                            module,
                            relative,
                            symbol,
                            int(call.lineno),
                            wrapper.kind,
                        )
                    )
                else:
                    for value in values:
                        requirements.add(
                            Requirement(wrapper.kind, value, "static", provenance)
                        )
    return tuple(sorted(requirements)), tuple(sorted(dynamic)), ()


__all__ = [
    "CompactCIDependencyError",
    "DynamicSite",
    "Provenance",
    "Requirement",
    "discover_module_requirements",
    "fixture_paths",
    "index_fixture_ownership",
    "load_dependency_manifest",
]
