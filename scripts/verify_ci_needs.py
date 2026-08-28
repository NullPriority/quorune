from __future__ import annotations

import json
import os
from typing import Iterable


KNOWN_DEPENDENCIES = frozenset(
    {
        "browser",
        "generated",
        "package",
        "plan",
        "python",
        "windows_certification",
    }
)


def required_dependencies(raw: str) -> tuple[str, ...]:
    value = json.loads(raw)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) for item in value)
        or len(value) != len(set(value))
        or not set(value).issubset(KNOWN_DEPENDENCIES)
        or "plan" not in value
    ):
        raise ValueError("required jobs must be a nonempty known list containing plan")
    return tuple(sorted(value))


def failed_dependencies(
    value: dict, required: Iterable[str] | None = None
) -> tuple[str, ...]:
    selected = tuple(value) if required is None else tuple(required)
    return tuple(
        sorted(
            name
            for name in selected
            for details in (value.get(name),)
            if not isinstance(details, dict) or details.get("result") != "success"
        )
    )


def main() -> int:
    raw = os.environ.get("CI_NEEDS_JSON")
    if not raw:
        print("CI_NEEDS_JSON is required")
        return 1
    value = json.loads(raw)
    if not isinstance(value, dict):
        print("CI_NEEDS_JSON must contain an object")
        return 1
    required_raw = os.environ.get("CI_REQUIRED_JOBS_JSON")
    if not required_raw:
        print("CI_REQUIRED_JOBS_JSON is required")
        return 1
    try:
        required = required_dependencies(required_raw)
    except (json.JSONDecodeError, ValueError) as exc:
        print(str(exc))
        return 1
    failed = failed_dependencies(value, required)
    print(json.dumps({"failed_dependencies": failed}, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
