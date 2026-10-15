from __future__ import annotations

import ast
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .rules.component_resolution import implementation_component_resolves
from .util import stable_json
from .work_selection import (
    WorkSelectionError,
    build_work_selection,
    load_work_selection_inputs,
    selected_work_candidate,
)


RULES_SCHEDULER_SCHEMA_VERSION = 2
_RULE_KEY = re.compile(r"^(?P<number>\d+)(?P<suffix>[a-z]*)$")


class RulesSchedulerError(ValueError):
    pass


def _hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RulesSchedulerError(f"Missing rules scheduler input: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RulesSchedulerError(
            f"Rules scheduler input must be an object: {path}"
        )
    return value


def _natural_rule_key(rule_id: str) -> tuple[tuple[int, str], ...]:
    parts = []
    for component in str(rule_id).split("."):
        match = _RULE_KEY.fullmatch(component)
        if match is None:
            raise RulesSchedulerError(f"Invalid rule id {rule_id!r}")
        parts.append(
            (
                int(match.group("number")),
                str(match.group("suffix") or ""),
            )
        )
    return tuple(parts)


def _ordered_unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value))


def _topological_subsystems(
    subsystems: Sequence[Mapping[str, Any]],
) -> list[str]:
    by_id = {str(row.get("id") or ""): row for row in subsystems}
    if "" in by_id or len(by_id) != len(subsystems):
        raise RulesSchedulerError(
            "Subsystem ids must be present and unique"
        )
    dependencies: dict[str, set[str]] = {}
    dependents: dict[str, set[str]] = defaultdict(set)
    for subsystem_id, row in by_id.items():
        declared = {
            str(value) for value in row.get("depends_on", [])
        }
        unknown = declared - set(by_id)
        if unknown:
            raise RulesSchedulerError(
                f"Subsystem {subsystem_id} has unknown dependencies: "
                f"{sorted(unknown)}"
            )
        if subsystem_id in declared:
            raise RulesSchedulerError(
                f"Subsystem {subsystem_id} depends on itself"
            )
        dependencies[subsystem_id] = declared
        for dependency in declared:
            dependents[dependency].add(subsystem_id)

    def order_key(subsystem_id: str) -> tuple[int, str]:
        return (
            int(by_id[subsystem_id].get("order") or 0),
            subsystem_id,
        )

    ready = sorted(
        (
            subsystem_id
            for subsystem_id, declared in dependencies.items()
            if not declared
        ),
        key=order_key,
    )
    ordered: list[str] = []
    while ready:
        subsystem_id = ready.pop(0)
        ordered.append(subsystem_id)
        for dependent in sorted(
            dependents.get(subsystem_id, set()), key=order_key
        ):
            dependencies[dependent].discard(subsystem_id)
            if not dependencies[dependent] and dependent not in ready:
                ready.append(dependent)
        ready.sort(key=order_key)
    if len(ordered) != len(by_id):
        cycle = sorted(
            subsystem_id
            for subsystem_id, declared in dependencies.items()
            if declared
        )
        raise RulesSchedulerError(
            f"Subsystem dependencies contain a cycle: {cycle}"
        )
    return ordered


def _active_profiles(
    values: Sequence[str],
    profile_priority: Sequence[str],
) -> list[str]:
    declared = {str(value) for value in values}
    if "all" in declared:
        declared = set(profile_priority)
    ordered = [
        profile for profile in profile_priority if profile in declared
    ]
    ordered.extend(sorted(declared - set(ordered)))
    return ordered


def _rules_related(first: str, second: str) -> bool:
    return (
        first == second
        or first.startswith(second + ".")
        or second.startswith(first + ".")
    )


def _capability_ids_by_rule(
    rule_ids: Sequence[str],
    capability_registry: Mapping[str, Any],
) -> dict[str, list[str]]:
    exact: dict[str, set[int]] = defaultdict(set)
    descendants: dict[str, set[int]] = defaultdict(set)
    capabilities = list(capability_registry.get('capabilities', []))
    for index, capability in enumerate(capabilities):
        if not capability.get('id'):
            continue
        for official in capability.get('official_rules', []):
            official = str(official)
            exact[official].add(index)
            for position, character in enumerate(official):
                if character == '.':
                    descendants[official[:position]].add(index)
    result: dict[str, list[str]] = {}
    for rule_id in rule_ids:
        matching = exact.get(rule_id, set()) | descendants.get(rule_id, set())
        for position, character in enumerate(rule_id):
            if character == '.':
                matching.update(exact.get(rule_id[:position], ()))
        result[rule_id] = sorted(str(capabilities[index]['id']) for index in matching)
    return result


def _scheduler_context(
    rule_index: Mapping[str, Any],
    conformance: Mapping[str, Any],
    catalog: Mapping[str, Any],
) -> dict[str, Any]:
    if int(catalog.get("schema_version") or 0) != 1:
        raise RulesSchedulerError("Unsupported rules subsystem catalog")
    rules = list(rule_index.get("rules", []))
    cases = list(conformance.get("cases", []))
    rules_by_id = {str(row.get("rule_id")): row for row in rules}
    cases_by_id = {str(row.get("rule_id")): row for row in cases}
    if len(rules_by_id) != len(rules):
        raise RulesSchedulerError("Rule index contains duplicate ids")
    if len(cases_by_id) != len(cases):
        raise RulesSchedulerError("Conformance corpus contains duplicate ids")
    if set(rules_by_id) != set(cases_by_id):
        raise RulesSchedulerError(
            "Rule index and conformance corpus cover different rule ids"
        )
    subsystems = list(catalog.get("subsystems", []))
    ordered_subsystem_ids = _topological_subsystems(subsystems)
    subsystem_by_id = {str(row["id"]): row for row in subsystems}
    section_to_subsystem: dict[str, str] = {}
    for subsystem in subsystems:
        for value in subsystem.get("section_ids", []):
            section_id = str(value)
            if section_id in section_to_subsystem:
                raise RulesSchedulerError(
                    f"Section {section_id} is assigned more than once"
                )
            section_to_subsystem[section_id] = str(subsystem["id"])
    indexed_sections = {
        str(rule.get("section", {}).get("id") or "")
        for rule in rules
    }
    missing = indexed_sections - set(section_to_subsystem)
    extra = set(section_to_subsystem) - indexed_sections
    if missing or extra:
        raise RulesSchedulerError(
            "Subsystem catalog section coverage mismatch: "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )
    queue_statuses = {
        str(value) for value in catalog.get("queue_statuses", [])
    }
    queue_classifications = {
        str(value)
        for value in catalog.get("queue_classifications", [])
    }
    queued_rule_ids = {
        rule_id
        for rule_id, case in cases_by_id.items()
        if str(case.get("status")) in queue_statuses
        and str(case.get("classification")) in queue_classifications
    }
    selected_source = dict(catalog.get("selected_batch") or {})
    selected_rule_ids = {
        str(value) for value in selected_source.get("rule_ids", [])
    }
    selected_subsystem_id = str(
        selected_source.get("subsystem_id") or ""
    )
    if selected_subsystem_id not in subsystem_by_id:
        raise RulesSchedulerError(
            "Selected batch must name a known subsystem"
        )
    if not selected_rule_ids:
        raise RulesSchedulerError(
            "Selected batch must contain at least one rule"
        )
    if not selected_rule_ids <= queued_rule_ids:
        raise RulesSchedulerError(
            "Selected batch includes a trusted, definition-only, or "
            "unknown rule: "
            f"{sorted(selected_rule_ids - queued_rule_ids)}"
        )
    return {
        "rules": rules,
        "cases": cases,
        "rules_by_id": rules_by_id,
        "cases_by_id": cases_by_id,
        "subsystems": subsystems,
        "ordered_subsystem_ids": ordered_subsystem_ids,
        "subsystem_by_id": subsystem_by_id,
        "section_to_subsystem": section_to_subsystem,
        "queued_rule_ids": queued_rule_ids,
        "profile_priority": [
            str(value)
            for value in catalog.get("active_profile_priority", [])
        ],
        "selected_source": selected_source,
        "selected_rule_ids": selected_rule_ids,
        "selected_subsystem_id": selected_subsystem_id,
    }


def _queue_items(
    context: Mapping[str, Any],
    capability_registry: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    rules = context["rules"]
    rules_by_id = context["rules_by_id"]
    cases_by_id = context["cases_by_id"]
    queued_rule_ids = context["queued_rule_ids"]
    selected_rule_ids = context["selected_rule_ids"]
    selected_subsystem_id = context["selected_subsystem_id"]
    capability_ids = _capability_ids_by_rule(
        list(rules_by_id), capability_registry
    )
    dependent_counts: Counter[str] = Counter()
    for rule in rules:
        for dependency in rule.get("dependency_ids", []):
            dependent_counts[str(dependency)] += 1
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rule_id in sorted(queued_rule_ids, key=_natural_rule_key):
        rule = rules_by_id[rule_id]
        case = cases_by_id[rule_id]
        section_id = str(rule.get("section", {}).get("id") or "")
        subsystem_id = context["section_to_subsystem"][section_id]
        if rule_id in selected_rule_ids and (
            subsystem_id != selected_subsystem_id
        ):
            raise RulesSchedulerError(
                f"Selected rule {rule_id} is outside "
                f"{selected_subsystem_id}"
            )
        dependencies = _ordered_unique(
            [
                str(value)
                for value in (
                    case.get("dependency_rule_ids")
                    or rule.get("dependency_ids", [])
                )
            ]
        )
        queued_dependencies = [
            value for value in dependencies if value in queued_rule_ids
        ]
        classification = str(case.get("classification") or "")
        work_state = (
            "behavioral_review_required"
            if classification == "unclassified"
            else "blocked_by_queued_rule"
            if queued_dependencies
            else "reviewed_behavioral_blocked"
        )
        result[subsystem_id].append(
            {
                "rule_id": rule_id,
                "section_id": section_id,
                "section_title": str(
                    rule.get("section", {}).get("title") or ""
                ),
                "classification": classification,
                "conformance_status": str(case.get("status") or ""),
                "assertion_kind": str(
                    case.get("assertion_kind") or ""
                ),
                "reviewed": bool(case.get("reviewed")),
                "work_state": work_state,
                "selected_batch": rule_id in selected_rule_ids,
                "dependency_rule_ids": dependencies,
                "queued_dependency_rule_ids": queued_dependencies,
                "dependent_rule_count": dependent_counts[rule_id],
                "implementation_components": _ordered_unique(
                    case.get("implementation_components", [])
                ),
                "executable_test_ids": _ordered_unique(
                    case.get("executable_test_ids", [])
                ),
                "active_profiles": _active_profiles(
                    rule.get("applicability_profiles", []),
                    context["profile_priority"],
                ),
                "compiler_impact": list(
                    context["subsystem_by_id"][subsystem_id].get(
                        "compiler_impact", []
                    )
                ),
                "capability_ids": capability_ids[rule_id],
                "blockers": _ordered_unique(case.get("blockers", [])),
                "source_span": rule.get("source_span"),
            }
        )
    return result


def _subsystem_rows(
    context: Mapping[str, Any],
    queued_by_subsystem: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows = []
    for index, subsystem_id in enumerate(
        context["ordered_subsystem_ids"], start=1
    ):
        source = context["subsystem_by_id"][subsystem_id]
        queue_items = queued_by_subsystem.get(subsystem_id, [])
        conformance_counts = Counter(
            item["conformance_status"] for item in queue_items
        )
        classification_counts = Counter(
            item["classification"] for item in queue_items
        )
        sections = set(source.get("section_ids", []))
        rows.append(
            {
                "schedule_order": index,
                "subsystem_id": subsystem_id,
                "title": str(source.get("title") or ""),
                "depends_on_subsystems": list(
                    source.get("depends_on", [])
                ),
                "section_ids": list(source.get("section_ids", [])),
                "compiler_impact": list(
                    source.get("compiler_impact", [])
                ),
                "active_profiles": list(context["profile_priority"]),
                "total_indexed_rules": sum(
                    str(rule.get("section", {}).get("id") or "")
                    in sections
                    for rule in context["rules"]
                ),
                "queued_rule_count": len(queue_items),
                "conformance_status_counts": dict(
                    sorted(conformance_counts.items())
                ),
                "classification_counts": dict(
                    sorted(classification_counts.items())
                ),
                "implementation_components": sorted(
                    {
                        value
                        for item in queue_items
                        for value in item["implementation_components"]
                    }
                ),
                "executable_test_ids": sorted(
                    {
                        value
                        for item in queue_items
                        for value in item["executable_test_ids"]
                    }
                ),
                "capability_ids": sorted(
                    {
                        value
                        for item in queue_items
                        for value in item["capability_ids"]
                    }
                ),
                "work_state": (
                    "selected_batch"
                    if subsystem_id == context["selected_subsystem_id"]
                    else "reviewed_blockers_present"
                    if conformance_counts.get("blocked", 0)
                    else "behavioral_review_required"
                    if queue_items
                    else "no_queued_rules"
                ),
                "rules": queue_items,
            }
        )
    return rows


def _selected_batch(
    context: Mapping[str, Any],
    queued_by_subsystem: Mapping[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    items = [
        item
        for item in queued_by_subsystem[
            context["selected_subsystem_id"]
        ]
        if item["rule_id"] in context["selected_rule_ids"]
    ]
    return {
        **context["selected_source"],
        "rule_ids": sorted(
            context["selected_rule_ids"], key=_natural_rule_key
        ),
        "implementation_components": sorted(
            {
                value
                for item in items
                for value in item["implementation_components"]
            }
        ),
        "executable_test_ids": sorted(
            {
                value
                for item in items
                for value in item["executable_test_ids"]
            }
        ),
        "rules": items,
    }


def _queue_summary(context: Mapping[str, Any]) -> dict[str, Any]:
    cases = context["cases"]
    status_counts = Counter(str(case.get("status") or "") for case in cases)
    classification_counts = Counter(
        str(case.get("classification") or "") for case in cases
    )
    return {
        "total_rules": len(context["rules"]),
        "queued_rules": len(context["queued_rule_ids"]),
        "reviewed_behavioral_blocked": sum(
            case.get("classification") == "behavioral"
            and case.get("status") == "blocked"
            for case in cases
        ),
        "behavioral_review_required": sum(
            case.get("classification") == "unclassified"
            and case.get("status") == "unreviewed"
            for case in cases
        ),
        "passing_behavioral": sum(
            case.get("classification") == "behavioral"
            and case.get("status") == "passing"
            for case in cases
        ),
        "status_counts": dict(sorted(status_counts.items())),
        "classification_counts": dict(
            sorted(classification_counts.items())
        ),
        "subsystem_count": len(context["subsystems"]),
    }


def _test_id_resolves(test_id: str, repository_root: Path) -> bool:
    """Resolve a fully qualified test by syntax only; never execute it."""

    parts = str(test_id).split(".")
    if len(parts) < 3 or parts[0] != "tests":
        return False
    module_path: Path | None = None
    remaining: list[str] = []
    for length in range(len(parts) - 1, 0, -1):
        candidate = repository_root.joinpath(*parts[:length]).with_suffix(".py")
        if candidate.is_file():
            module_path = candidate
            remaining = parts[length:]
            break
    if module_path is None or not remaining:
        return False
    try:
        tree = ast.parse(
            module_path.read_text(encoding="utf-8"),
            filename=str(module_path),
        )
    except (OSError, SyntaxError, UnicodeError):
        return False
    if len(remaining) == 1:
        return any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == remaining[0]
            for node in tree.body
        )
    if len(remaining) != 2:
        return False
    class_name, method_name = remaining
    return any(
        isinstance(node, ast.ClassDef)
        and node.name == class_name
        and any(
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name == method_name
            for child in node.body
        )
        for node in tree.body
    )


def _selected_bounded_batch_is_complete(
    context: Mapping[str, Any],
    capability_registry: Mapping[str, Any],
    *,
    repository_root: Path,
) -> bool:
    selected = context["selected_source"]
    target_ids = {
        str(value)
        for value in selected.get("target_capability_ids", [])
        if value
    }
    if not target_ids:
        return False
    capabilities = {
        str(row.get("id") or ""): row
        for row in capability_registry.get("capabilities", [])
    }
    targets = [capabilities.get(capability_id) for capability_id in target_ids]
    if any(target is None for target in targets):
        return False
    required_test_fields = {
        "positive": "positive_tests",
        "negative": "negative_tests",
        "interaction": "interaction_tests",
        "multiplayer": "multiplayer_tests",
        "privacy": "privacy_tests",
        "replay": "replay_tests",
    }
    for capability in targets:
        assert capability is not None
        required_evidence = {
            str(value) for value in capability.get("required_evidence", [])
        }
        if (
            capability.get("status") != "trusted"
            or capability.get("blockers")
            or not {"positive", "negative", "replay"} <= required_evidence
            or capability.get("implementation_mutation_status") != "killed"
        ):
            return False
        if any(
            evidence not in required_test_fields
            or not capability.get(required_test_fields[evidence])
            for evidence in required_evidence
        ):
            return False
        dependencies = tuple(capability.get("dependencies", ()))
        dependency_status = capability.get("dependency_fail_closed_status")
        if dependencies and dependency_status != "passed":
            return False
        if not dependencies and dependency_status not in {
            "passed",
            "not_applicable",
        }:
            return False
    selected_cases = [
        context["cases_by_id"][rule_id]
        for rule_id in context["selected_rule_ids"]
    ]
    components = {
        str(value)
        for capability in targets
        if capability is not None
        for value in capability.get("implementation_components", [])
    }
    components.update(
        str(value)
        for case in selected_cases
        for value in case.get("implementation_components", [])
    )
    tests = {
        str(value)
        for case in selected_cases
        for value in case.get("executable_test_ids", [])
    }
    exit_criteria = tuple(selected.get("exit_criteria", ()))
    return bool(
        components
        and tests
        and exit_criteria
        and all(type(value) is str and value for value in exit_criteria)
        and all(implementation_component_resolves(value) for value in components)
        and all(_test_id_resolves(value, repository_root) for value in tests)
    )


def build_rules_dependency_queue(
    rule_index: Mapping[str, Any],
    conformance: Mapping[str, Any],
    catalog: Mapping[str, Any],
    capability_registry: Mapping[str, Any],
    *,
    repository_root: str | Path | None = None,
    work_selection_inputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    context = _scheduler_context(rule_index, conformance, catalog)
    repository = (
        Path(repository_root)
        if repository_root is not None
        else Path(__file__).resolve().parents[1]
    )
    if _selected_bounded_batch_is_complete(
        context,
        capability_registry,
        repository_root=repository,
    ):
        batch_id = str(context["selected_source"].get("batch_id") or "")
        raise RulesSchedulerError(
            f"Selected batch {batch_id!r} is already complete; "
            "select an incomplete dependency-ready bounded batch"
        )
    queue_items = _queue_items(context, capability_registry)
    selected_batch = _selected_batch(context, queue_items)
    if work_selection_inputs is None:
        raise RulesSchedulerError(
            "Cross-program work-selection inputs are required"
        )
    payload: dict[str, Any] = {
        "schema_version": RULES_SCHEDULER_SCHEMA_VERSION,
        "scheduler_version": int(catalog.get("scheduler_version") or 0),
        "policy": str(catalog.get("policy") or ""),
        "effective_date": rule_index.get("effective_date"),
        "source_sha256": rule_index.get("source_sha256"),
        "source_fingerprints": {
            "rule_index": _hash(rule_index),
            "conformance": _hash(conformance),
            "subsystem_catalog": _hash(catalog),
            "capability_registry": _hash(capability_registry),
        },
        "active_profile_priority": context["profile_priority"],
        "unclassified_policy": str(
            catalog.get("unclassified_policy") or ""
        ),
        "summary": _queue_summary(context),
        "selected_batch": selected_batch,
        "subsystems": _subsystem_rows(context, queue_items),
    }
    payload["work_selection"] = build_work_selection(
        selected_batch=selected_batch,
        policy=dict(catalog.get("work_selection") or {}),
        inputs=work_selection_inputs,
    )
    payload["fingerprint"] = _hash(payload)
    return payload


def build_rules_dependency_queue_from_root(
    root: str | Path,
    *,
    harvest_outcome_history: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    repository = Path(root)
    work_inputs = load_work_selection_inputs(
        repository,
        harvest_outcome_history=harvest_outcome_history,
    )
    return build_rules_dependency_queue(
        _load(repository / "rules" / "rule-index.json"),
        _load(repository / "rules" / "conformance-cases.json"),
        _load(repository / "platform" / "rules-subsystems.json"),
        _load(
            repository
            / "quorune"
            / "rules"
            / "capability-registry.json"
        ),
        repository_root=repository,
        work_selection_inputs=work_inputs,
    )


def load_rules_dependency_queue(root: str | Path) -> dict[str, Any]:
    path = Path(root) / "coverage" / "rules-dependency-queue.json"
    value = _load(path)
    if int(value.get("schema_version") or 0) != RULES_SCHEDULER_SCHEMA_VERSION:
        raise RulesSchedulerError("Unsupported generated rules scheduler schema")
    work_selection = value.get("work_selection")
    if not isinstance(work_selection, Mapping):
        raise RulesSchedulerError(
            "Generated rules queue lacks cross-program work selection"
        )
    try:
        selected_work_candidate(work_selection)
    except WorkSelectionError as exc:
        raise RulesSchedulerError(str(exc)) from exc
    fingerprint = str(value.get("fingerprint") or "")
    unsigned = dict(value)
    unsigned.pop("fingerprint", None)
    if fingerprint != _hash(unsigned):
        raise RulesSchedulerError(
            "Rules dependency queue fingerprint does not match"
        )
    return value


def _compact_work_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": candidate.get("candidate_id"),
        "candidate_class": candidate.get("candidate_class"),
        "rank": candidate.get("rank"),
        "selection_state": candidate.get("selection_state"),
        "universal_subsystem": candidate.get("universal_subsystem"),
        "reusable_piece_count": len(candidate.get("reusable_piece_ids", [])),
        "rules_dependency_count": len(candidate.get("rules_dependency_ids", [])),
        "compiler_readiness": candidate.get("compiler_readiness"),
        "runtime_readiness": candidate.get("runtime_readiness"),
        "assurance_readiness": candidate.get("assurance_readiness"),
        "expected_complete_card_gain": candidate.get(
            "expected_complete_card_gain"
        ),
        "expected_material_residual_reduction": candidate.get(
            "expected_material_residual_reduction"
        ),
        "architecture_debt_removed": candidate.get(
            "architecture_debt_removed"
        ),
        "reranking_reason": candidate.get("reranking_reason"),
    }


def rules_next_work(
    root: str | Path,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    repository = Path(root)
    queue_path = repository / "coverage" / "rules-dependency-queue.json"
    if queue_path.is_file():
        queue = load_rules_dependency_queue(repository)
        selected = dict(queue["selected_batch"])
        next_rules = list(selected.pop("rules", []))
        work_selection = dict(queue.get("work_selection") or {})
        selected_work_id = work_selection.get("selected_candidate_id")
        selected_work = next(
            (
                dict(candidate)
                for candidate in work_selection.get("candidates", [])
                if candidate.get("candidate_id") == selected_work_id
            ),
            None,
        )
        return {
            "schema_version": RULES_SCHEDULER_SCHEMA_VERSION,
            "effective_date": queue.get("effective_date"),
            "scheduler_fingerprint": queue.get("fingerprint"),
            "selected_batch": selected,
            "selected_work": (
                _compact_work_candidate(selected_work)
                if selected_work is not None
                else None
            ),
            "work_candidates": [
                _compact_work_candidate(candidate)
                for candidate in list(work_selection.get("candidates", []))[
                    : max(1, int(limit))
                ]
            ],
            "next": next_rules[: max(1, int(limit))],
        }

    rule_index = _load(repository / "rules" / "rule-index.json")
    conformance = _load(
        repository / "rules" / "conformance-cases.json"
    )
    cases_by_rule = {
        str(case["rule_id"]): case
        for case in conformance.get("cases", [])
    }
    rules = list(rule_index.get("rules", []))
    children: Counter[str] = Counter()
    for rule in rules:
        for dependency in rule.get("dependency_ids", []):
            children[str(dependency)] += 1
    rank = {
        "failing": 0,
        "blocked": 1,
        "unreviewed": 2,
        "skipped": 3,
        "passing": 4,
        "definition_only": 5,
    }
    candidates = sorted(
        (
            {
                "rule_id": rule["rule_id"],
                "section": rule.get("section"),
                "coverage_status": rule.get("coverage_status"),
                "conformance_status": cases_by_rule.get(
                    str(rule["rule_id"]), {}
                ).get("status"),
                "classification": cases_by_rule.get(
                    str(rule["rule_id"]), {}
                ).get("classification"),
                "assertion_kind": cases_by_rule.get(
                    str(rule["rule_id"]), {}
                ).get("assertion_kind"),
                "dependent_rule_count": children[str(rule["rule_id"])],
                "source_span": rule.get("source_span"),
            }
            for rule in rules
            if cases_by_rule.get(str(rule["rule_id"]), {}).get("status")
            not in {"passing", "definition_only"}
        ),
        key=lambda row: (
            rank.get(str(row["conformance_status"]), 99),
            -int(row["dependent_rule_count"]),
            str(row["rule_id"]),
        ),
    )
    return {
        "schema_version": RULES_SCHEDULER_SCHEMA_VERSION,
        "effective_date": rule_index.get("effective_date"),
        "next": candidates[: max(1, int(limit))],
    }


def rules_dependency_queue_errors(root: str | Path) -> list[str]:
    try:
        expected = build_rules_dependency_queue_from_root(root)
        actual = load_rules_dependency_queue(root)
    except (
        OSError,
        json.JSONDecodeError,
        RulesSchedulerError,
        WorkSelectionError,
    ) as exc:
        return [str(exc)]
    if stable_json(actual) != stable_json(expected):
        return [
            "coverage/rules-dependency-queue.json is stale; run "
            "python scripts/update_rules_scheduler.py --write"
        ]
    return []
