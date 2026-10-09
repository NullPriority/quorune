from __future__ import annotations

import ast
from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


WRITE_CLASSIFICATIONS = (
    "canonical_mutation_owner_write",
    "orchestration_root_replacement",
    "compatibility_adapter_write",
    "grandfathered_engine_debt",
    "unowned_write",
    "false_positive_heuristic",
)
RUNTIME_TEXT_CLASSIFICATIONS = (
    "compiler_input",
    "generated_provenance",
    "display_only_metadata",
    "reviewed_historical_compatibility",
    "prohibited_runtime_interpretation",
)
MISSING_OWNER_PRIORITY = (
    "trigger_processing",
    "zones_and_object_identity",
    "turn_priority_and_decisions",
    "search_target_and_choice",
)
_RAW_TEXT_MEMBERS = {
    "oracle_text",
    "executable_oracle_text",
    "printed_oracle_text",
}


def _write_identity(record: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(record["file"]),
        str(record.get("symbol") or "<module>"),
        str(record["kind"]),
        str(record["state_path"]),
    )


def _identity_dict(identity: Sequence[str]) -> dict[str, str]:
    return dict(zip(("file", "symbol", "kind", "state_path"), identity))


def _module_classifications(
    value: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    return {
        str(row["file"]): row
        for row in value.get("modules", [])
        if isinstance(row, Mapping) and isinstance(row.get("file"), str)
    }


def _owner_matches(relative: str, owner: str) -> bool:
    normalized = owner.rstrip("/")
    if normalized.endswith(".py"):
        return relative == normalized
    return relative == normalized + ".py" or relative.startswith(normalized + "/")


def _subsystems_for_file(
    relative: str,
    source: Mapping[str, Any],
    classifications: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...]:
    known = {str(row["id"]) for row in source["subsystem_ownership"]}
    values = {
        str(row["id"])
        for row in source["subsystem_ownership"]
        if any(_owner_matches(relative, str(owner)) for owner in row["owners"])
    }
    classified = classifications.get(relative, {}).get("owning_subsystem")
    if classified in known:
        values.add(str(classified))
    return tuple(sorted(values))


def _engine_write_subsystems(record: Mapping[str, Any]) -> tuple[str, ...]:
    path = str(record.get("state_path") or "")
    symbol = str(record.get("symbol") or "")
    values: set[str] = set()
    if any(
        token in path
        for token in (
            "active_player",
            "current_turn",
            "extra_turns",
            "phase",
            "priority",
            "turn_sequence",
            "yield",
        )
    ) or any(token in symbol for token in ("turn", "step", "priority", "yield")):
        values.add("turn_priority_and_decisions")
    if any(token in path for token in ("cards", "zones")) or any(
        token in symbol for token in ("move_card", "zone", "copy_object")
    ):
        values.add("zones_and_object_identity")
    if "pending_trigger" in path or "trigger" in symbol:
        values.add("trigger_processing")
    if any(token in path for token in ("pending_decision", "choice")) or any(
        token in symbol for token in ("choice", "search", "target")
    ):
        values.add("search_target_and_choice")
    return tuple(sorted(values or {"legacy_engine"}))


def classify_state_writes(
    records: Iterable[Mapping[str, Any]],
    *,
    source: Mapping[str, Any],
    policy: Mapping[str, Any],
    baseline: Mapping[str, Any],
    module_classifications: Mapping[str, Any],
) -> dict[str, Any]:
    classifications = _module_classifications(module_classifications)
    mutable_owners = policy["game_state_access"]["mutable_owners"]
    rows: list[dict[str, Any]] = []
    for record in records:
        relative = str(record["file"])
        symbol = str(record.get("symbol") or "<module>")
        if relative == "quorune/engine.py" and (
            symbol == "transaction" and record.get("state_path") == "<state-root>"
        ):
            classification = "orchestration_root_replacement"
            rationale = "Authoritative transaction rollback replaces the state root."
        elif relative == "quorune/engine.py":
            classification = "grandfathered_engine_debt"
            rationale = "The legacy engine still mutates this represented state directly."
        elif relative == "quorune/record.py":
            classification = "compatibility_adapter_write"
            rationale = "Versioned record hydration or compatibility owns this write."
        elif relative in mutable_owners:
            classification = "canonical_mutation_owner_write"
            rationale = str(mutable_owners[relative])
        else:
            classification = "unowned_write"
            rationale = "No declared mutable owner covers this structural write."
        subsystems = (
            _engine_write_subsystems(record)
            if relative == "quorune/engine.py"
            else _subsystems_for_file(relative, source, classifications)
        )
        rows.append(
            {
                **record,
                "classification": classification,
                "owner_rationale": rationale,
                "subsystems": list(subsystems),
                "identity": _identity_dict(_write_identity(record)),
            }
        )
    rows.sort(
        key=lambda row: (
            row["file"],
            str(row.get("symbol")),
            row["kind"],
            row["state_path"],
        )
    )

    current = {_write_identity(row) for row in rows}
    previous = {
        _write_identity(row)
        for row in baseline.get("direct_game_state_write_identities", [])
    }
    added = sorted(current - previous)
    removed = sorted(previous - current)
    added_by_shape: dict[tuple[str, str], list[tuple[str, ...]]] = defaultdict(list)
    removed_by_shape: dict[tuple[str, str], list[tuple[str, ...]]] = defaultdict(list)
    for identity in added:
        added_by_shape[(identity[2], identity[3])].append(identity)
    for identity in removed:
        removed_by_shape[(identity[2], identity[3])].append(identity)
    migrated = []
    for shape in sorted(set(added_by_shape).intersection(removed_by_shape)):
        for before, after in zip(removed_by_shape[shape], added_by_shape[shape]):
            if before[:2] != after[:2]:
                migrated.append(
                    {"before": _identity_dict(before), "after": _identity_dict(after)}
                )

    by_classification = Counter(str(row["classification"]) for row in rows)
    by_file = Counter(str(row["file"]) for row in rows)
    by_symbol = Counter(
        f"{row['file']}::{row.get('symbol') or '<module>'}" for row in rows
    )
    by_subsystem = Counter(
        subsystem for row in rows for subsystem in row["subsystems"]
    )
    return {
        "classification_vocabulary": list(WRITE_CLASSIFICATIONS),
        "total_detected_writes": len(rows),
        "writes_in_commander_engine": sum(
            row["file"] == "quorune/engine.py" for row in rows
        ),
        "writes_in_canonical_owners": by_classification[
            "canonical_mutation_owner_write"
        ],
        "orchestration_root_replacements": by_classification[
            "orchestration_root_replacement"
        ],
        "compatibility_adapter_writes": by_classification[
            "compatibility_adapter_write"
        ],
        "grandfathered_engine_writes": by_classification[
            "grandfathered_engine_debt"
        ],
        "unowned_writes": by_classification["unowned_write"],
        "false_positive_writes": by_classification["false_positive_heuristic"],
        "by_classification": dict(sorted(by_classification.items())),
        "by_subsystem": dict(sorted(by_subsystem.items())),
        "by_file": dict(sorted(by_file.items())),
        "by_file_and_symbol": dict(sorted(by_symbol.items())),
        "newly_added_writes": [_identity_dict(row) for row in added],
        "removed_writes": [_identity_dict(row) for row in removed],
        "migrated_writes": migrated,
        "locations": rows,
    }


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _symbol(node: ast.AST, parents: Mapping[ast.AST, ast.AST]) -> str:
    values: list[str] = []
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            values.append(current.name)
    return ".".join(reversed(values)) or "<module>"


def _runtime_text_classification(relative: str, symbol: str) -> tuple[str, str]:
    compiler_prefixes = (
        "quorune/compiler/",
        "quorune/card_programs/",
    )
    compiler_files = {
        "quorune/abilities.py",
        "quorune/aura/grammar.py",
        "quorune/oracle_ir.py",
        "quorune/preflight.py",
    }
    display_files = {
        "quorune/carddb.py",
        "quorune/projection.py",
    }
    provenance_files = {
        "quorune/carddb_characteristics.py",
        "quorune/characteristic_evaluation.py",
    }
    historical_compatibility_files = {
        "quorune/card_overrides/game_record_v3.py",
    }
    if relative.startswith(compiler_prefixes) or relative in compiler_files:
        return "compiler_input", "Bounded compilation, validation, or corpus input."
    if relative == "quorune/mana.py" and symbol in {
        "effective_mana_record",
        "extract_effective_mana_modes",
        "extract_mana_modes",
        "_has_unresolved_dynamic_output",
    }:
        return (
            "compiler_input",
            "Legacy public compiler API retained for preflight and direct compiler tests; current game runtime consumes typed mana descriptors.",
        )
    if relative in display_files:
        return "display_only_metadata", "Card storage, inspection, or projection metadata."
    if relative in provenance_files:
        return "generated_provenance", "Carries derived text for identity or display provenance."
    if relative in historical_compatibility_files:
        return (
            "reviewed_historical_compatibility",
            "Explicit Game Record v3 compatibility isolated from current runtime behavior.",
        )
    return (
        "prohibited_runtime_interpretation",
        "Production runtime access can influence represented game behavior and must migrate to typed data.",
    )


def runtime_text_access_identity(record: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(record["file"]),
        str(record["symbol"]),
        str(record["access_kind"]),
        str(record["member"]),
    )


def runtime_text_accesses(
    analyses: Mapping[str, Any],
) -> dict[str, Any]:
    records: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for relative, analysis in analyses.items():
        tree = analysis.tree
        parents = _parents(tree)
        for node in ast.walk(tree):
            member: str | None = None
            access_kind: str | None = None
            if isinstance(node, ast.Attribute) and node.attr in _RAW_TEXT_MEMBERS:
                member = node.attr
                access_kind = "attribute"
            elif (
                isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value in _RAW_TEXT_MEMBERS
            ):
                member = str(node.slice.value)
                access_kind = "subscript"
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in _RAW_TEXT_MEMBERS
            ):
                member = str(node.args[0].value)
                access_kind = "mapping_get"
            elif (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in _RAW_TEXT_MEMBERS
            ):
                member = node.id
                access_kind = "local_name"
            if member is None or access_kind is None:
                continue
            symbol = _symbol(node, parents)
            classification, rationale = _runtime_text_classification(
                relative, symbol
            )
            identity = (relative, symbol, access_kind, member)
            row = records.setdefault(
                identity,
                {
                    "file": relative,
                    "symbol": symbol,
                    "access_kind": access_kind,
                    "member": member,
                    "classification": classification,
                    "rationale": rationale,
                    "lines": [],
                    "expressions": [],
                },
            )
            row["lines"].append(int(getattr(node, "lineno", 0)))
            expression = " ".join(ast.unparse(node).split())[:200]
            if expression not in row["expressions"]:
                row["expressions"].append(expression)
    rows = []
    for row in records.values():
        row["lines"] = sorted(set(row["lines"]))
        row["expressions"] = sorted(row["expressions"])
        rows.append(row)
    rows.sort(key=runtime_text_access_identity)
    counts = Counter(str(row["classification"]) for row in rows)
    prohibited = [
        row
        for row in rows
        if row["classification"] == "prohibited_runtime_interpretation"
    ]
    return {
        "classification_vocabulary": list(RUNTIME_TEXT_CLASSIFICATIONS),
        "total_accesses": len(rows),
        "by_classification": dict(sorted(counts.items())),
        "prohibited_runtime_interpretation_count": len(prohibited),
        "prohibited_runtime_interpretation": prohibited,
        "accesses": rows,
    }


def runtime_text_growth(
    inventory: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> list[dict[str, str]]:
    current = {
        runtime_text_access_identity(row)
        for row in inventory["prohibited_runtime_interpretation"]
    }
    allowed = {
        (
            str(row["file"]),
            str(row["symbol"]),
            str(row["access_kind"]),
            str(row["member"]),
        )
        for row in baseline.get("runtime_oracle_text_access_identities", [])
    }
    return [
        dict(zip(("file", "symbol", "access_kind", "member"), identity))
        for identity in sorted(current - allowed)
    ]


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _load_reusable_pieces(root: Path) -> Sequence[Mapping[str, Any]]:
    path = root / "coverage/reusable-piece-matrix.json.gz"
    if not path.is_file():
        return ()
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        value = json.load(handle)
    pieces = value.get("pieces", []) if isinstance(value, Mapping) else []
    return tuple(row for row in pieces if isinstance(row, Mapping))


def interaction_assurance_summary(root: Path) -> dict[str, Any]:
    path = root / "coverage/reusable-piece-interactions.json.gz"
    if not path.is_file():
        return {"available": False, "reason": f"missing {path.relative_to(root)}"}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        value = json.load(handle)
    summary = value.get("summary") if isinstance(value, Mapping) else None
    if not isinstance(summary, Mapping):
        raise ValueError(f"{path} must contain a summary object")
    applicable_high_risk = int(summary["applicable_high_risk_pairs"])
    covered_high_risk = int(summary["covered_high_risk_pairs"])
    return {
        "available": True,
        "source": str(path.relative_to(root)).replace("\\", "/"),
        "source_fingerprint": value.get("fingerprint"),
        "applicable_high_risk_pairs": applicable_high_risk,
        "covered_high_risk_pairs": covered_high_risk,
        "uncovered_high_risk_pairs": applicable_high_risk - covered_high_risk,
        "applicable_piece_pairs": int(summary["applicable_piece_pairs"]),
        "covered_piece_pairs": int(summary["covered_piece_pairs"]),
    }


def _module_name(relative: str) -> str:
    path = Path(relative).with_suffix("")
    parts = list(path.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _test_import_inventory(root: Path) -> tuple[tuple[str, frozenset[str]], ...]:
    """Read the current test import inventory once per architecture report."""

    values = []
    for path in sorted((root / "tests").glob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        values.append((path.stem, frozenset(imports)))
    return tuple(values)


def _tests_for_modules(root: Path, module_names: set[str], *,
                       inventory: Sequence[tuple[str, frozenset[str]]] | None = None) -> list[str]:
    return [
        name
        for name, imports in (_test_import_inventory(root) if inventory is None else inventory)
        if any(
            imported == module or imported.startswith(module + ".")
            for imported in imports
            for module in module_names
        )
    ]


def _engine_methods_for_subsystem(
    subsystem: str,
    engine: Mapping[str, Any],
) -> list[str]:
    groups = {
        str(row["id"]): list(row["methods"])
        for row in engine["responsibility_groups"]
    }
    if subsystem == "turn_priority_and_decisions":
        return sorted(groups.get("turn_priority_decisions", []))
    if subsystem == "zones_and_object_identity":
        return sorted(groups.get("zones_objects_and_state", []))
    semantic = groups.get("semantics_resolution_and_choices", [])
    if subsystem == "trigger_processing":
        return sorted(method for method in semantic if "trigger" in method)
    if subsystem == "search_target_and_choice":
        return sorted(
            method
            for method in semantic
            if any(token in method for token in ("choice", "search", "target", "apnap"))
        )
    return []


def _declared_runtime_text_subsystems(
    source: Mapping[str, Any],
    runtime_text: Mapping[str, Any],
) -> dict[tuple[str, str], str]:
    assignments: dict[tuple[str, str], str] = {}
    for owner in source["subsystem_ownership"]:
        subsystem = str(owner["id"])
        context = owner.get("context", {})
        if not isinstance(context, Mapping):
            raise ValueError(f"{subsystem} architecture context must be an object")
        declared = context.get("prohibited_runtime_oracle_text_symbols", [])
        if not isinstance(declared, list):
            raise ValueError(
                f"{subsystem} prohibited runtime Oracle-text symbols must be a list"
            )
        for value in declared:
            if not isinstance(value, Mapping) or set(value) != {"file", "symbol"}:
                raise ValueError(
                    f"{subsystem} runtime Oracle-text attribution must contain only "
                    "file and symbol"
                )
            relative = str(value["file"]).strip()
            symbol = str(value["symbol"]).strip()
            if not relative or not symbol:
                raise ValueError(
                    f"{subsystem} runtime Oracle-text attribution cannot be empty"
                )
            identity = (relative, symbol)
            previous = assignments.get(identity)
            if previous is not None:
                raise ValueError(
                    "Runtime Oracle-text symbol is assigned to multiple subsystems: "
                    f"{relative}::{symbol} ({previous}, {subsystem})"
                )
            assignments[identity] = subsystem

    known = {
        (str(row["file"]), str(row["symbol"]))
        for row in runtime_text["prohibited_runtime_interpretation"]
    }
    stale = sorted(set(assignments) - known)
    if stale:
        detail = ", ".join(f"{file}::{symbol}" for file, symbol in stale)
        raise ValueError(
            "Runtime Oracle-text subsystem attribution is stale or not prohibited: "
            + detail
        )
    return assignments


def build_subsystem_capsules(
    *,
    root: Path,
    source: Mapping[str, Any],
    analyses: Mapping[str, Any],
    module_classifications: Mapping[str, Any],
    writes: Mapping[str, Any],
    runtime_text: Mapping[str, Any],
    production: Mapping[str, Any],
    engine: Mapping[str, Any],
    exceptions: Mapping[str, Any],
) -> list[dict[str, Any]]:
    classifications = _module_classifications(module_classifications)
    pieces = _load_reusable_pieces(root)
    oversized_functions = production["oversized_functions_and_methods"]
    declared_text_subsystems = _declared_runtime_text_subsystems(
        source, runtime_text
    )
    test_imports = _test_import_inventory(root)
    capsules = []
    for owner in source["subsystem_ownership"]:
        subsystem = str(owner["id"])
        modules = sorted(
            relative
            for relative in analyses
            if any(
                _owner_matches(relative, str(candidate))
                for candidate in owner["owners"]
            )
            or classifications.get(relative, {}).get("owning_subsystem") == subsystem
        )
        # ``CommanderEngine`` remains an orchestration/debt owner for several
        # capsules.  Treating the whole module as implementation evidence for
        # each capsule would make every engine import, runtime-text read, test,
        # and reusable piece appear to belong to all of them.  Only the narrow
        # engine method inventory below is attributed to an individual capsule.
        implementation_modules = [
            relative for relative in modules if relative != "quorune/engine.py"
        ]
        module_names = {_module_name(relative) for relative in implementation_modules}
        piece_rows = [
            piece
            for piece in pieces
            if any(
                any(
                    str(component) == module
                    or str(component).startswith(module + ".")
                    for module in module_names
                )
                for component in piece.get("implementation_components", [])
            )
        ]
        capability_ids = sorted(
            {
                str(value)
                for piece in piece_rows
                for value in piece.get("source_ids", {}).get("capability", [])
            }
        )
        state_writes = [
            row
            for row in writes["locations"]
            if subsystem in row["subsystems"]
        ]
        context = owner.get("context", {})
        related_exceptions = [
            str(row["exception_id"])
            for row in exceptions.get("exceptions", [])
            if any(module in json.dumps(row, sort_keys=True) for module in modules)
        ]
        engine_methods = _engine_methods_for_subsystem(subsystem, engine)
        engine_method_set = set(engine_methods)
        text_accesses = [
            row
            for row in runtime_text["accesses"]
            if (
                declared_text_subsystems.get(
                    (str(row["file"]), str(row["symbol"]))
                )
                == subsystem
            )
            or (
                (str(row["file"]), str(row["symbol"]))
                not in declared_text_subsystems
                and (
                    row["file"] in implementation_modules
                    or (
                        row["file"] == "quorune/engine.py"
                        and str(row["symbol"]).rsplit(".", 1)[-1]
                        in engine_method_set
                    )
                )
            )
        ]
        reusable_piece_ids = sorted(str(row["piece_id"]) for row in piece_rows)
        primary_tests = _tests_for_modules(root, module_names, inventory=test_imports)
        oversized_symbols = [
            row
            for row in oversized_functions
            if row["file"] in implementation_modules
            or (
                row["file"] == "quorune/engine.py"
                and str(row["symbol"]).rsplit(".", 1)[-1] in engine_method_set
            )
        ]
        capsules.append(
            {
                "id": subsystem,
                "responsibility": context.get(
                    "responsibility",
                    subsystem.replace("_", " ").capitalize() + ".",
                ),
                "status": owner["status"],
                "missing_dedicated_owner": bool(owner["missing_dedicated_owner"]),
                "current_owners": list(owner["owners"]),
                "modules": modules,
                "allowed_dependency_layers": sorted(
                    {
                        str(layer)
                        for module in modules
                        for layer in classifications.get(module, {}).get(
                            "allowed_dependency_layers", []
                        )
                    }
                ),
                "state_authority": {
                    "classified_module_access": dict(
                        sorted(
                            Counter(
                                str(classifications[module]["game_state_access"])
                                for module in modules
                                if module in classifications
                            ).items()
                        )
                    ),
                    "direct_write_count": len(state_writes),
                    "engine_direct_write_count": sum(
                        row["file"] == "quorune/engine.py" for row in state_writes
                    ),
                },
                "public_ports_and_protocols": context.get("public_ports_and_protocols", []),
                "upstream_dependencies": context.get("upstream_dependencies", []),
                "downstream_dependencies": context.get("downstream_dependencies", []),
                "events_consumed": context.get("events_consumed", []),
                "events_produced": context.get("events_produced", []),
                "continuations": context.get("continuations", []),
                "compiler_cardprogram_representations": context.get(
                    "compiler_cardprogram_representations", []
                ),
                "capability_count": len(capability_ids),
                "capabilities": capability_ids[:100],
                "reusable_piece_count": len(reusable_piece_ids),
                "reusable_pieces": reusable_piece_ids[:100],
                "primary_test_count": len(primary_tests),
                "primary_tests": primary_tests[:100],
                "replay_behavior": context.get("replay_behavior", "not explicitly declared"),
                "privacy_sensitivity": context.get(
                    "privacy_sensitivity", "not explicitly declared"
                ),
                "adrs": context.get("adrs", []),
                "architecture_exceptions": sorted(related_exceptions),
                "oversized_symbol_count": len(oversized_symbols),
                "oversized_symbols": oversized_symbols[:100],
                "engine_methods_still_assigned": engine_methods,
                "runtime_oracle_text_accesses": len(text_accesses),
                "prohibited_runtime_oracle_text_accesses": sum(
                    row["classification"] == "prohibited_runtime_interpretation"
                    for row in text_accesses
                ),
                "current_debt": context.get(
                    "current_debt",
                    "Dedicated ownership is incomplete."
                    if owner["missing_dedicated_owner"]
                    else "No missing-owner flag is set.",
                ),
                "removal_condition": context.get(
                    "removal_condition",
                    "Remove remaining engine responsibility through a typed owner with focused replay and privacy evidence."
                    if owner["missing_dedicated_owner"]
                    else "No missing-owner removal condition applies.",
                ),
            }
        )
    return sorted(capsules, key=lambda row: row["id"])


def build_migration_queue(capsules: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(row["id"]): row for row in capsules}
    queue = []
    for subsystem in MISSING_OWNER_PRIORITY:
        row = by_id[subsystem]
        if not row["missing_dedicated_owner"]:
            continue
        priority = len(queue) + 1
        risk = 3 if row["privacy_sensitivity"] != "not explicitly declared" else 1
        score = (
            len(row["engine_methods_still_assigned"]) * 5
            + int(row["state_authority"]["engine_direct_write_count"]) * 8
            + int(row["oversized_symbol_count"]) * 6
            + int(row["prohibited_runtime_oracle_text_accesses"]) * 10
            + max(0, len(row["current_owners"]) - 1) * 2
            + len(row["downstream_dependencies"]) * 3
            + risk * 4
            + int(row["reusable_piece_count"])
        )
        queue.append(
            {
                "priority": priority,
                "subsystem": subsystem,
                "score": score,
                "dependency_reason": (
                    "Trigger processing is first because it contains live Oracle-text interpretation and produces events consumed by later zone, turn, and choice owners."
                    if subsystem == "trigger_processing"
                    else "Verified missing-owner order from the architecture stabilization directive."
                ),
                "engine_methods": len(row["engine_methods_still_assigned"]),
                "engine_direct_writes": row["state_authority"]["engine_direct_write_count"],
                "oversized_symbols": row["oversized_symbol_count"],
                "runtime_oracle_text_reads": row[
                    "prohibited_runtime_oracle_text_accesses"
                ],
                "duplicate_owner_paths": max(0, len(row["current_owners"]) - 1),
                "downstream_universal_systems": len(row["downstream_dependencies"]),
                "replay_privacy_risk": risk,
                "interaction_leverage": row["reusable_piece_count"],
                "removal_condition": row["removal_condition"],
            }
        )
    return queue


__all__ = [
    "MISSING_OWNER_PRIORITY",
    "RUNTIME_TEXT_CLASSIFICATIONS",
    "WRITE_CLASSIFICATIONS",
    "build_migration_queue",
    "build_subsystem_capsules",
    "classify_state_writes",
    "interaction_assurance_summary",
    "runtime_text_access_identity",
    "runtime_text_accesses",
    "runtime_text_growth",
]
