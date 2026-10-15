from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import re
from types import SimpleNamespace
from typing import Any, Iterable, Mapping, Sequence

try:
    from scripts.source_syntax import SyntaxSnapshot, analysis_syntax
except ModuleNotFoundError:
    from source_syntax import SyntaxSnapshot, analysis_syntax


PROHIBITED_CLASSIFICATION = "prohibited_identity_dispatch"
HISTORICAL_CLASSIFICATION = "reviewed_historical_compatibility"
OVERRIDE_CLASSIFICATION = "reviewed_override"


@dataclass(frozen=True)
class IdentityOrigin:
    node: ast.AST
    source_field: str
    source_kind: str


@dataclass(frozen=True)
class FlowRecord:
    flow_id: str
    classification: str
    file: str
    symbol: str | None
    line: int
    source_line: int
    source_field: str
    source_kind: str
    sink_kind: str
    selector: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "flow_id": self.flow_id,
            "classification": self.classification,
            "file": self.file,
            "symbol": self.symbol,
            "line": self.line,
            "source_line": self.source_line,
            "source_field": self.source_field,
            "source_kind": self.source_kind,
            "sink_kind": self.sink_kind,
            "selector": self.selector,
        }


@dataclass(frozen=True)
class _StaticValue:
    value: Any
    expression: str


def _policy(value: Mapping[str, Any]) -> Mapping[str, Any]:
    flow = value.get("card_identity_flow", value)
    if not isinstance(flow, Mapping) or int(flow.get("schema_version", 0)) != 1:
        raise ValueError("Unsupported card-identity-flow policy schema")
    return flow


def _classification_by_file(
    module_classifications: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    rows: Any = module_classifications
    if isinstance(module_classifications, Mapping):
        rows = module_classifications.get("modules", [])
    return {
        str(row["file"]): row
        for row in rows
        if isinstance(row, Mapping) and row.get("file")
    }


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _symbol(node: ast.AST, parents: Mapping[ast.AST, ast.AST]) -> str | None:
    names: list[str] = []
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(current.name)
    return ".".join(reversed(names)) or None


def _scope_nodes(scope: ast.AST) -> tuple[ast.AST, ...]:
    result: list[ast.AST] = []

    def visit(node: ast.AST, *, root: bool = False) -> None:
        if not root and isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            return
        result.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(scope, root=True)
    return tuple(result)


def _annotation_tokens(
    node: ast.AST | None, *, syntax: SyntaxSnapshot | None = None,
) -> frozenset[str]:
    if node is None:
        return frozenset()
    walk = ast.walk if syntax is None else syntax.walk
    return frozenset(
        child.id
        for child in walk(node)
        if isinstance(child, ast.Name)
    ) | frozenset(
        child.attr
        for child in walk(node)
        if isinstance(child, ast.Attribute)
    )


def _root_name(node: ast.AST) -> str | None:
    current = node
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


def _target_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(
            name for child in node.elts for name in _target_names(child)
        )
    return ()


def _static_value(
    node: ast.AST,
    environment: Mapping[str, _StaticValue],
) -> _StaticValue | None:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (str, int, float, bytes)):
            return _StaticValue(node.value, repr(node.value))
        return None
    if isinstance(node, ast.Name):
        if node.id in environment:
            return environment[node.id]
        if node.id.isupper():
            return _StaticValue(("symbol", node.id), node.id)
        return None
    if isinstance(node, ast.Attribute):
        rendered = _call_name(node)
        if rendered and node.attr.isupper():
            return _StaticValue(("symbol", rendered), rendered)
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_value(node.left, environment)
        right = _static_value(node.right, environment)
        if left is None or right is None:
            return None
        try:
            combined = left.value + right.value
        except TypeError:
            return None
        return _StaticValue(combined, f"{left.expression} + {right.expression}")
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = [_static_value(child, environment) for child in node.elts]
        if any(value is None for value in values):
            return None
        concrete = tuple(value.value for value in values if value is not None)
        return _StaticValue(concrete, ast.unparse(node))
    if isinstance(node, ast.Dict):
        keys = [_static_value(child, environment) for child in node.keys if child]
        if len(keys) != len(node.keys) or any(value is None for value in keys):
            return None
        return _StaticValue(
            ("static_mapping", tuple(value.value for value in keys if value)),
            ast.unparse(node),
        )
    if isinstance(node, ast.Call):
        name = _call_name(node.func)
        if name in {"tuple", "list", "set", "frozenset"} and len(node.args) == 1:
            value = _static_value(node.args[0], environment)
            if value is not None:
                return _StaticValue(value.value, ast.unparse(node))
    return None


def _fixed_identity_selector(value: _StaticValue | None) -> bool:
    if value is None:
        return False
    if isinstance(value.value, str):
        return True
    if isinstance(value.value, tuple):
        if value.value and value.value[0] in {"symbol", "static_mapping"}:
            return True
        return any(isinstance(item, str) for item in value.value)
    return False


def _identity_receiver_names(
    scope: ast.AST,
    nodes: Sequence[ast.AST],
    policy: Mapping[str, Any],
    *, syntax: SyntaxSnapshot | None = None,
) -> set[str]:
    receiver_types = set(map(str, policy["identity_receiver_types"]))
    returning_calls = set(map(str, policy["identity_returning_calls"]))
    names: set[str] = set()
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        arguments = [
            *scope.args.posonlyargs,
            *scope.args.args,
            *scope.args.kwonlyargs,
        ]
        if scope.args.vararg:
            arguments.append(scope.args.vararg)
        if scope.args.kwarg:
            arguments.append(scope.args.kwarg)
        for argument in arguments:
            if receiver_types & _annotation_tokens(argument.annotation, syntax=syntax):
                names.add(argument.arg)
    for node in nodes:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if receiver_types & _annotation_tokens(node.annotation, syntax=syntax):
                names.add(node.target.id)
        value: ast.AST | None = None
        targets: Sequence[ast.AST] = ()
        if isinstance(node, ast.Assign):
            value = node.value
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            value = node.value
            targets = (node.target,)
        if isinstance(value, ast.Call):
            name = _call_name(value.func)
            if name and name.rsplit(".", 1)[-1] in returning_calls:
                for target in targets:
                    names.update(_target_names(target))
    return names


class _ScopeAnalyzer:
    def __init__(
        self,
        *,
        relative: str,
        tree: ast.Module,
        scope: ast.AST,
        parents: Mapping[ast.AST, ast.AST],
        policy: Mapping[str, Any],
        module_classification: Mapping[str, Any],
        module_static: Mapping[str, _StaticValue],
        syntax: SyntaxSnapshot | None = None,
    ) -> None:
        self.relative = relative
        self.tree = tree
        self.scope = scope
        self.policy = policy
        self.module_classification = module_classification
        self.parents = parents
        self.syntax = SyntaxSnapshot(tree) if syntax is None else syntax
        self.nodes = _scope_nodes(scope)
        self.static: dict[str, _StaticValue] = dict(module_static)
        self.taints: dict[str, tuple[IdentityOrigin, ...]] = {}
        self.map_aliases: dict[str, str] = {}
        self.structural_occurrences: Counter[str] = Counter()
        self.identity_receivers = _identity_receiver_names(
            scope, self.nodes, policy, syntax=self.syntax
        )
        self.direct_origins: dict[int, IdentityOrigin] = {}

    def _direct_origin(self, node: ast.AST) -> IdentityOrigin | None:
        existing = self.direct_origins.get(id(node))
        if existing is not None:
            return existing
        source_fields = set(map(str, self.policy["identity_source_fields"]))
        mapping_fields = set(map(str, self.policy["mapping_source_fields"]))
        field: str | None = None
        kind: str | None = None
        if isinstance(node, ast.Attribute) and node.attr in source_fields:
            if node.attr != "name" or _root_name(node.value) in self.identity_receivers:
                field = node.attr
                kind = "attribute"
        elif isinstance(node, ast.Subscript):
            key = _static_value(node.slice, self.static)
            if key and isinstance(key.value, str) and key.value in mapping_fields:
                if key.value != "name" or _root_name(node.value) in self.identity_receivers:
                    field = key.value
                    kind = "mapping_subscript"
        elif isinstance(node, ast.Call):
            call = _call_name(node.func)
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
            ):
                key = _static_value(node.args[0], self.static)
                if key and isinstance(key.value, str) and key.value in mapping_fields:
                    if key.value != "name" or _root_name(node.func.value) in self.identity_receivers:
                        field = key.value
                        kind = "mapping_get"
            elif call == "getattr" and len(node.args) >= 2:
                key = _static_value(node.args[1], self.static)
                if key and isinstance(key.value, str) and key.value in source_fields:
                    if key.value != "name" or _root_name(node.args[0]) in self.identity_receivers:
                        field = key.value
                        kind = "getattr"
        if field is None or kind is None:
            return None
        origin = IdentityOrigin(node=node, source_field=field, source_kind=kind)
        self.direct_origins[id(node)] = origin
        return origin

    def _expr_taints(self, node: ast.AST | None) -> tuple[IdentityOrigin, ...]:
        if node is None:
            return ()
        direct = self._direct_origin(node)
        if direct is not None:
            return (direct,)
        if isinstance(node, ast.Name):
            return self.taints.get(node.id, ())
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            normalizers = set(map(str, self.policy["normalization_calls"]))
            if isinstance(node.func, ast.Name) and name in normalizers:
                return self._merge_taints(
                    *(self._expr_taints(arg) for arg in node.args)
                )
            if isinstance(node.func, ast.Attribute) and node.func.attr in normalizers:
                return self._merge_taints(
                    self._expr_taints(node.func.value),
                    *(self._expr_taints(arg) for arg in node.args),
                )
            return ()
        return self._merge_taints(
            *(self._expr_taints(child) for child in ast.iter_child_nodes(node))
        )

    @staticmethod
    def _merge_taints(
        *values: Iterable[IdentityOrigin],
    ) -> tuple[IdentityOrigin, ...]:
        unique: dict[tuple[int, str, str], IdentityOrigin] = {}
        for value in values:
            for origin in value:
                unique[(id(origin.node), origin.source_field, origin.source_kind)] = origin
        return tuple(unique[key] for key in sorted(unique, key=repr))

    def _bind_target(
        self,
        target: ast.AST,
        value: ast.AST,
        taints: tuple[IdentityOrigin, ...],
    ) -> bool:
        changed = False
        if isinstance(target, ast.Name):
            if taints and self.taints.get(target.id) != taints:
                self.taints[target.id] = taints
                changed = True
            static = _static_value(value, self.static)
            if static is not None and self.static.get(target.id) != static:
                self.static[target.id] = static
                changed = True
            alias = self._implementation_map_name(value)
            if alias and self.map_aliases.get(target.id) != alias:
                self.map_aliases[target.id] = alias
                changed = True
            return changed
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(
            value, (ast.Tuple, ast.List)
        ):
            for child_target, child_value in zip(target.elts, value.elts):
                changed |= self._bind_target(
                    child_target, child_value, self._expr_taints(child_value)
                )
        return changed

    def _build_environments(self) -> None:
        assignments = [
            node
            for node in self.nodes
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
        ]
        for _ in range(min(8, len(assignments) + 2)):
            changed = False
            for node in assignments:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        changed |= self._bind_target(
                            target, node.value, self._expr_taints(node.value)
                        )
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    changed |= self._bind_target(
                        node.target, node.value, self._expr_taints(node.value)
                    )
                elif isinstance(node, ast.NamedExpr):
                    changed |= self._bind_target(
                        node.target, node.value, self._expr_taints(node.value)
                    )
            if not changed:
                break

    def _implementation_map_name(self, node: ast.AST) -> str | None:
        markers = tuple(
            str(value).casefold()
            for value in self.policy["implementation_map_markers"]
        )
        if isinstance(node, ast.Name):
            if node.id in self.map_aliases:
                return self.map_aliases[node.id]
            folded = node.id.casefold()
            if node.id.lstrip("_").isupper() and any(
                marker in folded for marker in markers
            ):
                return node.id
        if isinstance(node, ast.Attribute):
            rendered = _call_name(node)
            if (
                rendered
                and node.attr.lstrip("_").isupper()
                and any(marker in rendered.casefold() for marker in markers)
            ):
                return rendered
        if isinstance(node, ast.Dict):
            keys = [_static_value(key, self.static) for key in node.keys if key]
            callable_values = all(
                isinstance(value, (ast.Name, ast.Attribute, ast.Lambda))
                for value in node.values
            )
            if keys and len(keys) == len(node.keys) and all(keys) and callable_values:
                return "<static callable mapping>"
        return None

    def _allowed_classification(self, node: ast.AST) -> str:
        owner = str(self.module_classification.get("owning_subsystem") or "")
        specificity = str(
            self.module_classification.get("card_specificity_policy") or ""
        )
        if owner == self.policy["historical_compatibility_owner"]:
            return HISTORICAL_CLASSIFICATION
        if specificity == self.policy["reviewed_override_policy"]:
            return OVERRIDE_CLASSIFICATION
        if any(
            self.relative.startswith(str(prefix))
            for prefix in self.policy["compiler_binding_prefixes"]
        ) or self.relative in set(map(str, self.policy["compiler_binding_files"])):
            return "compiler_binding"
        symbol = (_symbol(node, self.parents) or "").casefold()
        if any(
            self.relative.startswith(str(prefix))
            for prefix in self.policy["generated_provenance_prefixes"]
        ) or any(marker in symbol for marker in self.policy["provenance_symbol_markers"]):
            return "generated_provenance"
        if any(
            self.relative.startswith(str(prefix))
            for prefix in self.policy["display_metadata_prefixes"]
        ) or self.relative in set(map(str, self.policy["display_metadata_files"])):
            return "display_metadata"
        return "rules_value"

    def _record(
        self,
        *,
        origin: IdentityOrigin,
        sink: ast.AST,
        sink_kind: str,
        selector: str | None,
        fixed_dispatch: bool,
    ) -> FlowRecord:
        allowed = self._allowed_classification(sink)
        compiler_face_binding = (
            allowed == "compiler_binding"
            and origin.source_field in {"active_face", "face_id"}
            and selector is not None
            and set(re.findall(r"[a-z_]+", selector.casefold()))
            <= {"front", "back"}
        )
        classification = (
            allowed
            if not fixed_dispatch
            or allowed in {HISTORICAL_CLASSIFICATION, OVERRIDE_CLASSIFICATION}
            or compiler_face_binding
            else PROHIBITED_CLASSIFICATION
        )
        structural = {
            "file": self.relative,
            "symbol": _symbol(sink, self.parents),
            "source_field": origin.source_field,
            "source_kind": origin.source_kind,
            "sink_kind": sink_kind,
            "origin": ast.dump(origin.node, include_attributes=False),
            "sink": ast.dump(sink, include_attributes=False),
        }
        normalized = json.dumps(
            structural, sort_keys=True, separators=(",", ":")
        )
        duplicate_ordinal = self.structural_occurrences[normalized]
        self.structural_occurrences[normalized] += 1
        structural["duplicate_ordinal"] = duplicate_ordinal
        flow_id = "card-identity-flow:" + hashlib.sha256(
            json.dumps(structural, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()[:20]
        return FlowRecord(
            flow_id=flow_id,
            classification=classification,
            file=self.relative,
            symbol=_symbol(sink, self.parents),
            line=int(getattr(sink, "lineno", 0)),
            source_line=int(getattr(origin.node, "lineno", 0)),
            source_field=origin.source_field,
            source_kind=origin.source_kind,
            sink_kind=sink_kind,
            selector=selector,
        )

    def analyze(self) -> tuple[FlowRecord, ...]:
        self._build_environments()
        records: list[FlowRecord] = []
        consumed: set[int] = set()
        for node in self.nodes:
            sink_taints: tuple[IdentityOrigin, ...] = ()
            sink_kind: str | None = None
            selector: str | None = None
            if isinstance(node, ast.Compare):
                left = node.left
                for operator, right in zip(node.ops, node.comparators):
                    left_taints = self._expr_taints(left)
                    right_taints = self._expr_taints(right)
                    left_static = _static_value(left, self.static)
                    right_static = _static_value(right, self.static)
                    if left_taints and not right_taints and _fixed_identity_selector(right_static):
                        sink_taints = self._merge_taints(sink_taints, left_taints)
                        selector = right_static.expression if right_static else None
                    if right_taints and not left_taints and _fixed_identity_selector(left_static):
                        sink_taints = self._merge_taints(sink_taints, right_taints)
                        selector = left_static.expression if left_static else None
                    left = right
                if sink_taints:
                    sink_kind = (
                        "static_membership"
                        if any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
                        else "static_identity_comparison"
                    )
            elif isinstance(node, ast.Match):
                subject = self._expr_taints(node.subject)
                fixed_patterns = [
                    child.value
                    for child in self.syntax.walk(node)
                    if isinstance(child, ast.Constant) and isinstance(child.value, str)
                ]
                if subject and fixed_patterns:
                    sink_taints = subject
                    sink_kind = "static_match_dispatch"
                    selector = repr(tuple(fixed_patterns))
            elif isinstance(node, ast.Subscript):
                mapping = self._implementation_map_name(node.value)
                key_taints = self._expr_taints(node.slice)
                if mapping and key_taints:
                    sink_taints = key_taints
                    sink_kind = "implementation_map_lookup"
                    selector = mapping
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
            ):
                mapping = self._implementation_map_name(node.func.value)
                key_taints = self._expr_taints(node.args[0])
                if mapping and key_taints:
                    sink_taints = key_taints
                    sink_kind = "implementation_map_lookup"
                    selector = mapping
            if sink_kind is not None:
                for origin in sink_taints:
                    consumed.add(id(origin.node))
                    records.append(
                        self._record(
                            origin=origin,
                            sink=node,
                            sink_kind=sink_kind,
                            selector=selector,
                            fixed_dispatch=True,
                        )
                    )

        for node in self.nodes:
            origin = self._direct_origin(node)
            if origin is None or id(origin.node) in consumed:
                continue
            records.append(
                self._record(
                    origin=origin,
                    sink=node,
                    sink_kind="identity_as_data",
                    selector=None,
                    fixed_dispatch=False,
                )
            )
        return tuple(records)


def _module_static_values(tree: ast.Module) -> dict[str, _StaticValue]:
    environment: dict[str, _StaticValue] = {}
    module_nodes = _scope_nodes(tree)
    assignments = [
        node for node in module_nodes if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    for _ in range(min(32, len(assignments) + 2)):
        changed = False
        for node in assignments:
            value = node.value
            if value is None:
                continue
            static = _static_value(value, environment)
            if static is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            for target in targets:
                for name in _target_names(target):
                    if environment.get(name) != static:
                        environment[name] = static
                        changed = True
        if not changed:
            break
    return environment


def analyze_identity_flows(
    analyses: Mapping[str, Any],
    architecture_policy: Mapping[str, Any],
    module_classifications: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    policy = _policy(architecture_policy)
    classifications = _classification_by_file(module_classifications)
    records: list[FlowRecord] = []
    for relative, analysis in sorted(analyses.items()):
        tree = analysis.tree
        if not isinstance(tree, ast.Module):
            raise ValueError(f"Identity-flow analysis requires an AST module: {relative}")
        module_static = _module_static_values(tree)
        syntax = analysis_syntax(analysis)
        parents = syntax.parents
        scopes: list[ast.AST] = [tree]
        scopes.extend(
            node
            for node in syntax.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        for scope in scopes:
            records.extend(
                _ScopeAnalyzer(
                    relative=relative,
                    tree=tree,
                    scope=scope,
                    parents=parents,
                    policy=policy,
                    module_classification=classifications.get(relative, {}),
                    module_static=module_static,
                    syntax=syntax,
                ).analyze()
            )
    unique = {record.flow_id: record for record in records}
    ordered = tuple(
        sorted(
            unique.values(),
            key=lambda row: (row.file, row.line, row.source_line, row.flow_id),
        )
    )
    counts = Counter(record.classification for record in ordered)
    observed_sink_kinds = {record.sink_kind for record in ordered}
    sink_vocabulary = set(map(str, policy["sink_kinds"]))
    unknown_sink_kinds = observed_sink_kinds - sink_vocabulary
    if unknown_sink_kinds:
        raise ValueError(
            "Identity-flow analysis emitted undeclared sinks: "
            + ", ".join(sorted(unknown_sink_kinds))
        )
    prohibited = [
        record.as_dict()
        for record in ordered
        if record.classification == PROHIBITED_CLASSIFICATION
    ]
    allowed_overrides = [
        record.as_dict()
        for record in ordered
        if record.classification in {HISTORICAL_CLASSIFICATION, OVERRIDE_CLASSIFICATION}
    ]
    return {
        "schema_version": 1,
        "invariant": (
            "Card identity is permitted as data, but fixed card identity may not "
            "select generic legality, mutation, implementation, or outcomes."
        ),
        "vocabulary": {
            "identity_source_fields": sorted(map(str, policy["identity_source_fields"])),
            "mapping_source_fields": sorted(map(str, policy["mapping_source_fields"])),
            "normalization_calls": sorted(map(str, policy["normalization_calls"])),
            "implementation_map_markers": sorted(
                map(str, policy["implementation_map_markers"])
            ),
            "sink_kinds": sorted(sink_vocabulary),
            "allowed_classifications": sorted(
                map(str, policy["allowed_classifications"])
            ),
            "prohibited_classification": PROHIBITED_CLASSIFICATION,
            "classification_vocabulary": sorted(
                set(map(str, policy["allowed_classifications"]))
                | {PROHIBITED_CLASSIFICATION}
            ),
        },
        "counts": {
            "classified_flow_count": len(ordered),
            "prohibited_identity_dispatch_count": len(prohibited),
            "by_classification": dict(sorted(counts.items())),
        },
        "classified_flows": [record.as_dict() for record in ordered],
        "prohibited_locations": prohibited,
        "allowed_override_locations": allowed_overrides,
        "limitations": list(map(str, policy["limitations"])),
        "stable_identity": (
            "flow_id hashes file, enclosing symbol, normalized source/sink AST, "
            "source and sink kinds, and duplicate structural ordinal; line numbers "
            "are display only"
        ),
        "external_dependencies": {
            "card_database": False,
            "card_name_index": False,
            "specificity_baseline": False,
        },
    }


def analyze_identity_source(
    source: str,
    *,
    relative: str,
    architecture_policy: Mapping[str, Any],
    module_classification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    tree = ast.parse(source, filename=relative)
    classifications = {
        "schema_version": 1,
        "modules": [
            {
                "file": relative,
                "owning_subsystem": "fixture",
                "card_specificity_policy": "generic_no_growth",
                **dict(module_classification or {}),
            }
        ],
    }
    return analyze_identity_flows(
        {relative: SimpleNamespace(tree=tree)},
        architecture_policy,
        classifications,
    )
