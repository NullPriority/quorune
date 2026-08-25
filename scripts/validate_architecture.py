from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable, Mapping

try:
    from scripts.architecture_observability import (
        classify_state_writes,
        runtime_text_accesses,
        runtime_text_growth,
    )
    from scripts.architecture_identity_flow import (
        analyze_identity_flows,
    )
    from scripts.update_architecture_audit import (
        MODULE_CLASSIFICATIONS,
        ROOT,
        _engine_metrics,
        _production_metrics,
        _state_and_dispatch_metrics,
        analyze_production,
    )
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    from architecture_observability import (
        classify_state_writes,
        runtime_text_accesses,
        runtime_text_growth,
    )
    from architecture_identity_flow import (
        analyze_identity_flows,
    )
    from update_architecture_audit import (
        MODULE_CLASSIFICATIONS,
        ROOT,
        _engine_metrics,
        _production_metrics,
        _state_and_dispatch_metrics,
        analyze_production,
    )

from quorune.semantics import VALID_EFFECT_OPERATIONS
from quorune.util import stable_json


POLICY = ROOT / "platform" / "architecture-policy.json"
BASELINE = ROOT / "platform" / "architecture-guard-baseline.json"
SOURCE_CHECK_DEFERRED_GUARDS = frozenset(
    {"module_classification_fingerprint"}
)


def _baseline_allowance_fingerprint(baseline: Mapping[str, Any]) -> str:
    payload = {
        key: baseline[key]
        for key in (
            "engine",
            "direct_game_state_writes_by_file",
            "direct_game_state_write_identities",
            "direct_game_state_write_classification_counts",
            "runtime_oracle_text_access_identities",
            "oracle_id_literals",
            "registered_effect_operations",
            "legacy_card_specific_operations",
            "card_named_helpers",
            "oversized_modules",
            "oversized_functions_and_methods",
        )
    }
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _identity(item: Mapping[str, Any], *fields: str) -> tuple[Any, ...]:
    return tuple(item.get(field) for field in fields)


def _counter_extras(
    current: Iterable[tuple[Any, ...]], allowed: Iterable[tuple[Any, ...]]
) -> list[tuple[Any, ...]]:
    remaining = Counter(allowed)
    extras: list[tuple[Any, ...]] = []
    for item in current:
        if remaining[item]:
            remaining[item] -= 1
        else:
            extras.append(item)
    return sorted(extras, key=repr)


def _matches_prefix(value: str, prefixes: Iterable[str]) -> bool:
    return any(value == prefix or value.startswith(prefix + ".") for prefix in prefixes)


def _protected(relative: str, policy: Mapping[str, Any]) -> bool:
    return relative in set(policy["protected_rules_modules"]) or any(
        relative.startswith(prefix) for prefix in policy["protected_future_prefixes"]
    )


def _game_state_imports(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and any(alias.name == "GameState" for alias in node.names)
        for node in ast.walk(tree)
    )


def forbidden_import_violations(
    analyses: Mapping[str, Any], policy: Mapping[str, Any]
) -> list[dict[str, str]]:
    global_forbidden = policy["forbidden_import_prefixes"]

    def forbidden_for(relative: str) -> list[str]:
        result = list(global_forbidden)
        for scope in policy.get("scoped_forbidden_imports", []):
            if relative.startswith(str(scope["path_prefix"])):
                result.extend(scope["import_prefixes"])
        return result

    return sorted(
        (
            {"file": relative, "import": imported}
            for relative, analysis in analyses.items()
            if _protected(relative, policy)
            for imported in analysis.imports
            if _matches_prefix(imported, forbidden_for(relative))
        ),
        key=lambda item: (item["file"], item["import"]),
    )


def mutation_ownership_violations(
    locations: Iterable[Mapping[str, Any]], mutable_owners: Iterable[str]
) -> list[Mapping[str, Any]]:
    owners = set(mutable_owners)
    return [item for item in locations if item["file"] not in owners]


def state_write_identity(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return _identity(item, "file", "symbol", "kind", "state_path")


def module_classification_failures(
    analyses: Mapping[str, Any], policy: Mapping[str, Any]
) -> list[dict[str, Any]]:
    path = ROOT / str(policy["module_classifications"])
    value = _load_json(path)
    required_top = {
        "schema_version",
        "classification_policy",
        "modules",
        "fingerprint",
    }
    failures: list[dict[str, Any]] = []
    if set(value) != required_top or value.get("schema_version") != 1:
        return [
            _failure(
                "module_classification_schema",
                "The default-deny module classification artifact is malformed.",
                sorted(set(value) ^ required_top),
            )
        ]
    payload = dict(value)
    fingerprint = str(payload.pop("fingerprint") or "")
    expected_fingerprint = hashlib.sha256(
        stable_json(payload).encode("utf-8")
    ).hexdigest()
    if fingerprint != expected_fingerprint:
        failures.append(
            _failure(
                "module_classification_fingerprint",
                "The module classification fingerprint is stale.",
                {"recorded": fingerprint, "expected": expected_fingerprint},
            )
        )
    rows = value.get("modules")
    if not isinstance(rows, list):
        return failures + [
            _failure(
                "module_classification_schema",
                "Module classifications must be a list.",
                type(rows).__name__,
            )
        ]
    required_row = {
        "file",
        "layer",
        "owning_subsystem",
        "allowed_dependency_layers",
        "game_state_access",
        "card_specificity_policy",
        "visibility_sensitivity",
        "replay_participation",
    }
    by_file: dict[str, Mapping[str, Any]] = {}
    malformed = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != required_row:
            malformed.append(index)
            continue
        relative = str(row["file"])
        if relative in by_file:
            malformed.append(index)
            continue
        by_file[relative] = row
    if malformed:
        failures.append(
            _failure(
                "module_classification_schema",
                "Module classification rows are malformed or duplicated.",
                malformed,
            )
        )
    current_files = set(analyses)
    classified_files = set(by_file)
    if current_files != classified_files:
        failures.append(
            _failure(
                "module_classification_default_deny",
                "Every production Python module must be classified exactly once.",
                {
                    "unclassified": sorted(current_files - classified_files),
                    "removed": sorted(classified_files - current_files),
                },
            )
        )
    valid_layers = {
        "domain",
        "rules",
        "semantics",
        "adapter",
        "application",
        "transport",
    }
    invalid_rows = []
    for relative, row in by_file.items():
        allowed = row["allowed_dependency_layers"]
        if (
            row["layer"] not in valid_layers
            or not isinstance(allowed, list)
            or not set(allowed).issubset(valid_layers)
            or row["game_state_access"]
            not in {"none", "read_only", "mutable_owner", "model_definition"}
            or row["card_specificity_policy"]
            not in {"generic_no_growth", "explicit_card_override"}
            or row["visibility_sensitivity"]
            not in {"authoritative_internal", "principal_scoped"}
            or row["replay_participation"] not in {"none", "authoritative"}
        ):
            invalid_rows.append(relative)
    if invalid_rows:
        failures.append(
            _failure(
                "module_classification_values",
                "Module classification values are outside the closed vocabulary.",
                sorted(invalid_rows),
            )
        )
    module_to_file = {
        analysis.module: relative for relative, analysis in analyses.items()
    }
    dependency_violations = []
    for relative, analysis in analyses.items():
        row = by_file.get(relative)
        if row is None:
            continue
        allowed = set(row["allowed_dependency_layers"])
        for imported in analysis.imports:
            candidate = imported
            while candidate and candidate not in module_to_file and "." in candidate:
                candidate = candidate.rsplit(".", 1)[0]
            target_file = module_to_file.get(candidate)
            target = by_file.get(target_file or "")
            if target is not None and target["layer"] not in allowed:
                dependency_violations.append(
                    {
                        "file": relative,
                        "layer": row["layer"],
                        "import": imported,
                        "target_layer": target["layer"],
                    }
                )
    if dependency_violations:
        failures.append(
            _failure(
                "module_dependency_layers",
                "A classified module imports a disallowed dependency layer.",
                sorted(
                    dependency_violations,
                    key=lambda item: (item["file"], item["import"]),
                ),
            )
        )
    return failures


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
    ).stdout.strip()


def build_baseline(baseline_commit: str) -> dict[str, Any]:
    source, paths, analyses = analyze_production()
    production = _production_metrics(paths, analyses, source)
    engine = _engine_metrics(analyses, source)
    state = _state_and_dispatch_metrics(analyses, source)
    policy = _load_json(POLICY)
    module_classifications = _load_json(MODULE_CLASSIFICATIONS)
    write_inventory = classify_state_writes(
        state["direct_game_state_write_heuristic"]["locations"],
        source=source,
        policy=policy,
        baseline={},
        module_classifications=module_classifications,
    )
    runtime_text = runtime_text_accesses(analyses)
    methods = [
        {
            "name": item["name"],
            "kind": item["kind"],
            "visibility": item["visibility"],
        }
        for item in analyses["quorune/engine.py"].functions
        if item["kind"] == "method"
    ]
    result = {
        "schema_version": 4,
        "baseline_commit": baseline_commit,
        "purpose": (
            "Exact residual architecture allowances; fixed card-identity dispatch "
            "has no allowance and must remain zero."
        ),
        "engine": {
            "physical_lines": engine["physical_lines"],
            "logical_lines": engine["logical_lines"],
            "methods": sorted(methods, key=lambda item: item["name"]),
        },
        "direct_game_state_writes_by_file": dict(
            sorted(
                Counter(
                    item["file"]
                    for item in state["direct_game_state_write_heuristic"][
                        "locations"
                    ]
                ).items()
            )
        ),
        "direct_game_state_write_identities": [
            {
                "file": file,
                "symbol": symbol,
                "kind": kind,
                "state_path": state_path,
            }
            for file, symbol, kind, state_path in sorted(
                {
                    (
                        item["file"],
                        item.get("symbol"),
                        item["kind"],
                        item["state_path"],
                    )
                    for item in state[
                        "direct_game_state_write_heuristic"
                    ]["locations"]
                },
                key=repr,
            )
        ],
        "direct_game_state_write_classification_counts": write_inventory[
            "by_classification"
        ],
        "runtime_oracle_text_access_identities": [
            {
                "file": row["file"],
                "symbol": row["symbol"],
                "access_kind": row["access_kind"],
                "member": row["member"],
            }
            for row in runtime_text["prohibited_runtime_interpretation"]
        ],
        "oracle_id_literals": state["oracle_id_literals"]["locations"],
        "registered_effect_operations": sorted(VALID_EFFECT_OPERATIONS),
        "legacy_card_specific_operations": sorted(
            source["card_specific_semantic_operations"]
        ),
        "card_named_helpers": source["card_named_helpers"],
        "oversized_modules": sorted(
            (
                {
                    "file": item["file"],
                    "logical_lines": item["logical_lines"],
                }
                for item in production["oversized_modules"]
            ),
            key=lambda item: item["file"],
        ),
        "oversized_functions_and_methods": sorted(
            (
                {
                    "file": item["file"],
                    "symbol": item["symbol"],
                    "logical_lines": item["logical_lines"],
                }
                for item in production["oversized_functions_and_methods"]
            ),
            key=lambda item: (item["file"], item["symbol"]),
        ),
    }
    return result


def bind_baseline_exception(
    baseline: dict[str, Any],
    *,
    exception_id: str,
    adr: str,
) -> dict[str, Any]:
    baseline["exception_binding"] = {
        "exception_id": exception_id,
        "adr": adr,
        "allowance_fingerprint": _baseline_allowance_fingerprint(baseline),
    }
    return baseline


def exception_binding_failures(
    policy: Mapping[str, Any], baseline: Mapping[str, Any]
) -> list[dict[str, Any]]:
    registry = _load_json(ROOT / str(policy["exception_registry"]))
    if registry.get("schema_version") != 1 or not isinstance(
        registry.get("exceptions"), list
    ):
        return [
            _failure(
                "architecture_exception_schema",
                "The architecture exception registry is malformed.",
                registry,
            )
        ]
    binding = baseline.get("exception_binding")
    if not isinstance(binding, Mapping):
        return [
            _failure(
                "architecture_exception_binding",
                "The guard baseline lacks an exact exception binding.",
                binding,
            )
        ]
    matches = [
        row
        for row in registry["exceptions"]
        if isinstance(row, Mapping)
        and row.get("exception_id") == binding.get("exception_id")
    ]
    if len(matches) != 1:
        return [
            _failure(
                "architecture_exception_binding",
                "The baseline exception ID must resolve exactly once.",
                binding,
            )
        ]
    row = matches[0]
    required = {
        "exception_id",
        "adr",
        "artifact",
        "allowance_fingerprint",
        "exact_allowance",
        "rationale",
        "owner",
        "maximum_scope",
        "removal_condition",
        "target_milestone",
        "replay_implications",
        "privacy_implications",
        "security_implications",
        "tests",
    }
    failures = []
    if set(row) != required:
        failures.append(
            _failure(
                "architecture_exception_schema",
                "The bound exception does not use the exact reviewed schema.",
                sorted(set(row) ^ required),
            )
        )
        return failures
    current = _baseline_allowance_fingerprint(baseline)
    if (
        row["adr"] != binding.get("adr")
        or row["artifact"] != policy["baseline"]
        or row["allowance_fingerprint"] != current
        or binding.get("allowance_fingerprint") != current
    ):
        failures.append(
            _failure(
                "architecture_exception_binding",
                "The exception is not bound to this exact allowance payload.",
                {
                    "binding": binding,
                    "registry_allowance_fingerprint": row[
                        "allowance_fingerprint"
                    ],
                    "current_allowance_fingerprint": current,
                },
            )
        )
    adr = ROOT / str(row["adr"])
    if not adr.is_file() or not all(
        str(row[field]).strip()
        for field in (
            "exact_allowance",
            "rationale",
            "owner",
            "maximum_scope",
            "removal_condition",
            "target_milestone",
            "replay_implications",
            "privacy_implications",
            "security_implications",
        )
    ):
        failures.append(
            _failure(
                "architecture_exception_metadata",
                "The bound ADR or removal metadata is incomplete.",
                row,
            )
        )
    return failures


def _failure(guard: str, detail: str, evidence: Any) -> dict[str, Any]:
    return {"guard": guard, "detail": detail, "evidence": evidence}


def _dependency_and_mutation_failures(
    policy: Mapping[str, Any],
    baseline: Mapping[str, Any],
    analyses: Mapping[str, Any],
    state: Mapping[str, Any],
    write_inventory: Mapping[str, Any],
    runtime_text: Mapping[str, Any],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    import_violations = forbidden_import_violations(analyses, policy)
    if import_violations:
        failures.append(
            _failure(
                "forbidden_imports",
                "Rules/domain code imports a transport, application, persistence, or AI layer.",
                import_violations,
            )
        )
    state_policy = policy["game_state_access"]
    allowed_state_access = {
        *state_policy["mutable_owners"],
        *state_policy["read_only_consumers"],
        state_policy["model_definition"],
    }
    access_violations = sorted(
        relative
        for relative, analysis in analyses.items()
        if _game_state_imports(analysis.tree) and relative not in allowed_state_access
    )
    if access_violations:
        failures.append(
            _failure(
                "game_state_access",
                "A module outside the declared owners/readers imports GameState.",
                access_violations,
            )
        )
    state_locations = state["direct_game_state_write_heuristic"]["locations"]
    write_counts = Counter(item["file"] for item in state_locations)
    owner_modules = set(state_policy["mutable_owners"])
    nonowner_writes = mutation_ownership_violations(
        state_locations, owner_modules
    )
    if nonowner_writes:
        failures.append(
            _failure(
                "mutation_ownership",
                "A direct GameState write is outside a declared mutable owner.",
                nonowner_writes,
            )
        )
    current_write_identities = {
        state_write_identity(item)
        for item in state_locations
    }
    allowed_write_identities = {
        state_write_identity(item)
        for item in baseline["direct_game_state_write_identities"]
    }
    new_write_identities = sorted(
        current_write_identities - allowed_write_identities,
        key=repr,
    )
    if new_write_identities:
        failures.append(
            _failure(
                "mutation_identity_non_growth",
                "A new structural GameState write identity appeared.",
                new_write_identities,
            )
        )
    write_growth = {
        file: {"baseline": allowed, "current": write_counts[file]}
        for file, allowed in baseline["direct_game_state_writes_by_file"].items()
        if write_counts[file] > int(allowed)
    }
    if write_growth:
        failures.append(
            _failure(
                "mutation_non_growth",
                "Direct GameState write sites grew beyond the Phase 1 baseline.",
                write_growth,
            )
        )
    baseline_classifications = baseline[
        "direct_game_state_write_classification_counts"
    ]
    engine_write_growth = int(write_inventory["writes_in_commander_engine"]) - int(
        baseline_classifications.get("grandfathered_engine_debt", 0)
        + baseline_classifications.get("orchestration_root_replacement", 0)
    )
    if engine_write_growth > 0:
        failures.append(
            _failure(
                "engine_mutation_non_growth",
                "CommanderEngine gained a direct GameState-write identity.",
                {"delta": engine_write_growth},
            )
        )
    if int(write_inventory["unowned_writes"]):
        failures.append(
            _failure(
                "unowned_state_mutation",
                "A direct GameState write has no declared owner.",
                [
                    row
                    for row in write_inventory["locations"]
                    if row["classification"] == "unowned_write"
                ],
            )
        )
    runtime_growth = runtime_text_growth(runtime_text, baseline)
    if runtime_growth:
        failures.append(
            _failure(
                "runtime_oracle_text_interpretation_growth",
                "A new production runtime Oracle-text interpretation site appeared.",
                runtime_growth,
            )
        )
    return failures


def _card_identity_failures(
    policy: Mapping[str, Any],
    baseline: Mapping[str, Any],
    source: Mapping[str, Any],
    analyses: Mapping[str, Any],
    state: Mapping[str, Any],
    module_classifications: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    failures: list[dict[str, Any]] = []
    identity_flow = analyze_identity_flows(
        analyses, policy, module_classifications
    )
    prohibited = identity_flow["prohibited_locations"]
    if prohibited:
        failures.append(
            _failure(
                "card_identity_flow",
                "Fixed card identity selects generic implementation, legality, mutation, or outcome.",
                prohibited,
            )
        )
    current_oracle_ids = [
        _identity(
            item,
            "file",
            "symbol",
            "value",
            "oracle_id",
            "in_condition",
        )
        for item in state["oracle_id_literals"]["locations"]
    ]
    allowed_oracle_ids = [
        _identity(
            item,
            "file",
            "symbol",
            "value",
            "oracle_id",
            "in_condition",
        )
        for item in baseline["oracle_id_literals"]
    ]
    new_oracle_ids = _counter_extras(current_oracle_ids, allowed_oracle_ids)
    if new_oracle_ids:
        failures.append(
            _failure(
                "oracle_id_literals",
                "A new Oracle-ID literal appeared in production code.",
                new_oracle_ids,
            )
        )
    current_methods = {
        (item["name"], item["kind"], item["visibility"])
        for item in analyses["quorune/engine.py"].functions
        if item["kind"] == "method"
    }
    baseline_methods = {
        (item["name"], item["kind"], item["visibility"])
        for item in baseline["engine"]["methods"]
    }
    new_methods = sorted(current_methods - baseline_methods)
    if new_methods:
        failures.append(
            _failure(
                "commander_engine_methods",
                "CommanderEngine gained a method instead of extracting responsibility.",
                new_methods,
            )
        )
    new_effect_operations = sorted(
        set(VALID_EFFECT_OPERATIONS) - set(baseline["registered_effect_operations"])
    )
    if new_effect_operations:
        failures.append(
            _failure(
                "semantic_operations",
                "A new universal semantic operation lacks Phase 1 architecture review.",
                new_effect_operations,
            )
        )
    new_card_operations = sorted(
        set(source["card_specific_semantic_operations"])
        - set(baseline["legacy_card_specific_operations"])
    )
    if new_card_operations:
        failures.append(
            _failure(
                "card_named_operations",
                "A new card-specific operation was classified in the universal executor.",
                new_card_operations,
            )
        )
    new_card_helpers = [
        item
        for item in source["card_named_helpers"]
        if item not in baseline["card_named_helpers"]
    ]
    if new_card_helpers:
        failures.append(
            _failure(
                "card_named_helpers",
                "A new card-specific helper was added to the kernel baseline.",
                new_card_helpers,
            )
        )
    return failures, {
        "prohibited_identity_dispatch_count": int(
            identity_flow["counts"]["prohibited_identity_dispatch_count"]
        ),
        "current_oracle_ids": len(current_oracle_ids),
        "allowed_oracle_ids": len(allowed_oracle_ids),
    }


def _size_debt_failures(
    policy: Mapping[str, Any],
    baseline: Mapping[str, Any],
    production: Mapping[str, Any],
    engine: Mapping[str, Any],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    threshold = policy["review_thresholds"]
    engine_growth = engine["logical_lines"] - int(baseline["engine"]["logical_lines"])
    if engine_growth > int(threshold["engine_net_logical_growth"]):
        failures.append(
            _failure(
                "engine_growth",
                "CommanderEngine exceeded the reviewed net-growth allowance.",
                {"logical_line_delta": engine_growth},
            )
        )
    baseline_modules = {
        item["file"]: int(item["logical_lines"])
        for item in baseline["oversized_modules"]
    }
    current_modules = {
        item["file"]: int(item["logical_lines"])
        for item in production["oversized_modules"]
    }
    new_oversized_modules = sorted(
        set(current_modules) - set(baseline_modules)
    )
    if new_oversized_modules:
        failures.append(
            _failure(
                "oversized_modules",
                "A new production module exceeds the review threshold without a baseline ADR.",
                new_oversized_modules,
            )
        )
    grown_oversized_modules = {
        file: {
            "baseline": baseline_modules[file],
            "current": current_modules[file],
        }
        for file in sorted(set(current_modules) & set(baseline_modules))
        if current_modules[file] > baseline_modules[file]
    }
    if grown_oversized_modules:
        failures.append(
            _failure(
                "oversized_module_non_growth",
                "An existing oversized production module grew.",
                grown_oversized_modules,
            )
        )
    current_oversized_functions = {
        (item["file"], item["symbol"]): int(item["logical_lines"])
        for item in production["oversized_functions_and_methods"]
    }
    baseline_functions = {
        (item["file"], item["symbol"]): int(item["logical_lines"])
        for item in baseline["oversized_functions_and_methods"]
    }
    new_oversized_functions = sorted(
        set(current_oversized_functions) - set(baseline_functions)
    )
    if new_oversized_functions:
        failures.append(
            _failure(
                "oversized_functions",
                "A new function exceeds the review threshold without a baseline ADR.",
                new_oversized_functions,
            )
        )
    grown_oversized_functions = {
        f"{file}::{symbol}": {
            "baseline": baseline_functions[(file, symbol)],
            "current": current_oversized_functions[(file, symbol)],
        }
        for file, symbol in sorted(
            set(current_oversized_functions) & set(baseline_functions)
        )
        if current_oversized_functions[(file, symbol)]
        > baseline_functions[(file, symbol)]
    }
    if grown_oversized_functions:
        failures.append(
            _failure(
                "oversized_function_non_growth",
                "An existing oversized function or method grew.",
                grown_oversized_functions,
            )
        )
    return failures


def _guard_metrics(
    baseline: Mapping[str, Any],
    engine: Mapping[str, Any],
    state: Mapping[str, Any],
    identity: Mapping[str, int],
    write_inventory: Mapping[str, Any],
    runtime_text: Mapping[str, Any],
) -> dict[str, Any]:
    state_locations = state["direct_game_state_write_heuristic"]["locations"]
    baseline_writes = sum(baseline["direct_game_state_writes_by_file"].values())
    engine_growth = engine["logical_lines"] - int(baseline["engine"]["logical_lines"])
    metrics = {
        "engine_logical_lines": {
            "baseline": baseline["engine"]["logical_lines"],
            "current": engine["logical_lines"],
            "delta": engine_growth,
        },
        "direct_game_state_writes": {
            "baseline": baseline_writes,
            "current": len(state_locations),
            "delta": len(state_locations) - baseline_writes,
        },
        "engine_local_direct_game_state_writes": {
            "baseline": int(
                baseline["direct_game_state_write_classification_counts"].get(
                    "grandfathered_engine_debt", 0
                )
            )
            + int(
                baseline["direct_game_state_write_classification_counts"].get(
                    "orchestration_root_replacement", 0
                )
            ),
            "current": write_inventory["writes_in_commander_engine"],
            "delta": write_inventory["writes_in_commander_engine"]
            - int(
                baseline["direct_game_state_write_classification_counts"].get(
                    "grandfathered_engine_debt", 0
                )
            )
            - int(
                baseline["direct_game_state_write_classification_counts"].get(
                    "orchestration_root_replacement", 0
                )
            ),
        },
        "canonical_owner_direct_game_state_writes": {
            "baseline": int(
                baseline["direct_game_state_write_classification_counts"].get(
                    "canonical_mutation_owner_write", 0
                )
            ),
            "current": write_inventory["writes_in_canonical_owners"],
            "delta": write_inventory["writes_in_canonical_owners"]
            - int(
                baseline["direct_game_state_write_classification_counts"].get(
                    "canonical_mutation_owner_write", 0
                )
            ),
        },
        "unowned_direct_game_state_writes": {
            "baseline": 0,
            "current": write_inventory["unowned_writes"],
            "delta": write_inventory["unowned_writes"],
        },
        "prohibited_runtime_oracle_text_accesses": {
            "baseline": len(baseline["runtime_oracle_text_access_identities"]),
            "current": runtime_text["prohibited_runtime_interpretation_count"],
            "delta": runtime_text["prohibited_runtime_interpretation_count"]
            - len(baseline["runtime_oracle_text_access_identities"]),
        },
        "prohibited_identity_dispatch_count": {
            "baseline": 0,
            "current": identity["prohibited_identity_dispatch_count"],
            "delta": identity["prohibited_identity_dispatch_count"],
        },
        "oracle_id_literals": {
            "baseline": identity["allowed_oracle_ids"],
            "current": identity["current_oracle_ids"],
            "delta": identity["current_oracle_ids"]
            - identity["allowed_oracle_ids"],
        },
        "registered_effect_operations": {
            "baseline": len(baseline["registered_effect_operations"]),
            "current": len(VALID_EFFECT_OPERATIONS),
            "delta": len(VALID_EFFECT_OPERATIONS)
            - len(baseline["registered_effect_operations"]),
        },
    }
    return metrics


def evaluate_architecture() -> dict[str, Any]:
    policy = _load_json(POLICY)
    baseline = _load_json(ROOT / str(policy["baseline"]))
    source, paths, analyses = analyze_production()
    production = _production_metrics(paths, analyses, source)
    engine = _engine_metrics(analyses, source)
    state = _state_and_dispatch_metrics(analyses, source)
    module_classifications = _load_json(MODULE_CLASSIFICATIONS)
    write_inventory = classify_state_writes(
        state["direct_game_state_write_heuristic"]["locations"],
        source=source,
        policy=policy,
        baseline=baseline,
        module_classifications=module_classifications,
    )
    runtime_text = runtime_text_accesses(analyses)
    failures = _dependency_and_mutation_failures(
        policy, baseline, analyses, state, write_inventory, runtime_text
    )
    failures.extend(exception_binding_failures(policy, baseline))
    failures.extend(module_classification_failures(analyses, policy))
    identity_failures, identity = _card_identity_failures(
        policy,
        baseline,
        source,
        analyses,
        state,
        module_classifications,
    )
    failures.extend(identity_failures)
    failures.extend(_size_debt_failures(policy, baseline, production, engine))
    return {
        "schema_version": 1,
        "policy_version": policy["policy_version"],
        "baseline_commit": baseline["baseline_commit"],
        "evaluated_commit": _git_head(),
        "status": "pass" if not failures else "fail",
        "metrics": _guard_metrics(
            baseline, engine, state, identity, write_inventory, runtime_text
        ),
        "failures": failures,
    }


def source_policy_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Keep source-policy failures while deferring generated fingerprints."""

    failures = list(result["failures"])
    deferred = [
        row
        for row in failures
        if row["guard"] in SOURCE_CHECK_DEFERRED_GUARDS
    ]
    current = [row for row in failures if row not in deferred]
    value = dict(result)
    value["failures"] = current
    value["deferred_generated_failures"] = deferred
    value["status"] = "pass" if not current else "fail"
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--source-check", action="store_true")
    mode.add_argument("--initialize-baseline", action="store_true")
    mode.add_argument("--refresh-baseline", action="store_true")
    parser.add_argument("--baseline-commit")
    parser.add_argument("--adr")
    parser.add_argument("--exception-id")
    args = parser.parse_args()
    if args.initialize_baseline:
        if BASELINE.exists():
            parser.error(
                "baseline already exists; reviewed updates must be made with an ADR"
            )
        if not args.baseline_commit:
            parser.error("--initialize-baseline requires --baseline-commit")
        value = build_baseline(args.baseline_commit)
        BASELINE.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(json.dumps({"ok": True, "baseline": str(BASELINE)}, indent=2))
        return 0
    if args.refresh_baseline:
        if not args.baseline_commit or not args.adr or not args.exception_id:
            parser.error(
                "--refresh-baseline requires --baseline-commit, --adr, and "
                "--exception-id"
            )
        adr = (ROOT / args.adr).resolve()
        if not adr.is_file() or ROOT not in adr.parents:
            parser.error("--adr must name an existing repository ADR")
        value = bind_baseline_exception(
            build_baseline(args.baseline_commit),
            exception_id=args.exception_id,
            adr=adr.relative_to(ROOT).as_posix(),
        )
        BASELINE.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(json.dumps({"ok": True, "baseline": str(BASELINE)}, indent=2))
        return 0
    if args.baseline_commit:
        parser.error(
            "--baseline-commit is only valid with baseline initialization "
            "or reviewed refresh"
        )
    if args.adr or args.exception_id:
        parser.error("--adr/--exception-id require --refresh-baseline")
    result = evaluate_architecture()
    if args.source_check:
        result = source_policy_result(result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
