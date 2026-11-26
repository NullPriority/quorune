from __future__ import annotations

from typing import Any, Mapping


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
