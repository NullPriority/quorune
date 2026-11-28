from __future__ import annotations

from typing import Any, Callable, Mapping


REGISTRY_FIELDS = {
    "schema_version",
    "registry_version",
    "effective_date",
    "source_sha256",
    "profiles",
    "aggregates",
    "capabilities",
}
AGGREGATE_FIELDS = {"mechanic_id", "capabilities"}
CAPABILITY_FIELDS = {
    "id",
    "version",
    "official_rules",
    "supported_profiles",
    "applicability",
    "dependencies",
    "implementation_components",
    "positive_tests",
    "negative_tests",
    "interaction_tests",
    "multiplayer_tests",
    "privacy_tests",
    "replay_tests",
    "required_evidence",
    "dependency_fail_closed_status",
    "dependency_fail_closed_rationale",
    "implementation_mutation_status",
    "implementation_mutation_rationale",
    "blockers",
    "status",
}
APPLICABILITY_FIELDS = {"summary", "inputs", "outputs", "exclusions"}


def require_exact_capability_fields(
    value: Mapping[str, Any],
    expected: set[str],
    *,
    field: str,
    error_type: type[ValueError],
) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise error_type(
            f"{field} is missing required fields: {', '.join(missing)}"
        )
    if unknown:
        raise error_type(
            f"{field} has unknown fields: {', '.join(unknown)}"
        )


def validate_capability_graph(
    capabilities: Mapping[str, Mapping[str, Any]],
    aggregates: Mapping[str, tuple[str, ...]],
    error_type: type[ValueError],
) -> None:
    """Validate registry references and reject cyclic leaf dependencies."""
    known = set(capabilities)
    for capability_id, row in capabilities.items():
        missing = sorted(set(row["dependencies"]) - known)
        if missing:
            raise error_type(
                f"{capability_id} has unknown dependencies: "
                + ", ".join(missing)
            )
    for mechanic_id, dependencies in aggregates.items():
        missing = sorted(set(dependencies) - known)
        if missing:
            raise error_type(
                f"{mechanic_id} aggregate has unknown capabilities: "
                + ", ".join(missing)
            )

    visiting: list[str] = []
    complete: set[str] = set()

    def visit(capability_id: str) -> None:
        if capability_id in complete:
            return
        if capability_id in visiting:
            start = visiting.index(capability_id)
            cycle = [*visiting[start:], capability_id]
            raise error_type(
                "Capability dependency cycle: " + " -> ".join(cycle)
            )
        visiting.append(capability_id)
        for dependency in capabilities[capability_id][
            "dependencies"
        ]:
            visit(str(dependency))
        visiting.pop()
        complete.add(capability_id)

    for capability_id in sorted(capabilities):
        visit(capability_id)


def validate_trusted_capability(
    capability_id: str, row: Mapping[str, Any], *,
    dependency_status: str, implementation_mutation_status: str,
    resolve_component: Callable[[str], bool], error_type: type[ValueError],
    minimum_evidence: frozenset[str], evidence_fields: Mapping[str, str],
) -> None:
    """Validate proof obligations for one trusted capability leaf."""
    if row["blockers"]:
        raise error_type(
            f"Trusted {capability_id} cannot retain blockers"
        )
    components = row["implementation_components"]
    if not components:
        raise error_type(
            f"Trusted {capability_id} requires an implementation"
        )
    if not any(
        resolve_component(component)
        for component in components
    ):
        raise error_type(
            f"Trusted {capability_id} requires a resolvable "
            "implementation component"
        )
    if row["dependencies"] and dependency_status != "passed":
        raise error_type(
            f"Trusted {capability_id} requires passed dependency "
            "fail-closed evidence"
        )
    if implementation_mutation_status != "killed":
        raise error_type(
            f"Trusted {capability_id} requires killed implementation "
            "mutation evidence"
        )
    missing_minimum = minimum_evidence - set(
        row["required_evidence"]
    )
    if missing_minimum:
        raise error_type(
            f"Trusted {capability_id} must require minimum evidence: "
            + ", ".join(sorted(missing_minimum))
        )
    for evidence in row["required_evidence"]:
        field = evidence_fields[evidence]
        if not row[field]:
            raise error_type(
                f"Trusted {capability_id} requires {field}"
            )
