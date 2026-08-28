from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


MAIN_BROAD_WORKFLOW = ".github/workflows/main-broad.yml"
RECOVERY_LABEL = "main-red-recovery"


class MainHealthError(ValueError):
    pass


def latest_completed_main_broad(value: Any) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping) or not isinstance(
        value.get("workflow_runs"), list
    ):
        raise MainHealthError("main broad workflow-run response is malformed")
    candidates = [
        row
        for row in value["workflow_runs"]
        if isinstance(row, Mapping)
        and row.get("path") == MAIN_BROAD_WORKFLOW
        and row.get("event") == "push"
        and row.get("status") == "completed"
        and type(row.get("id")) is int
    ]
    return max(candidates, key=lambda row: int(row["id"])) if candidates else None


def recovery_requested(event: Any) -> bool:
    pull_request = event.get("pull_request") if isinstance(event, Mapping) else None
    labels = (
        pull_request.get("labels", [])
        if isinstance(pull_request, Mapping)
        else []
    )
    return any(
        isinstance(label, Mapping)
        and str(label.get("name", "")).casefold() == RECOVERY_LABEL
        for label in labels
    )


def verify_main_health(value: Any, *, allow_recovery: bool) -> dict[str, Any]:
    latest = latest_completed_main_broad(value)
    if latest is None:
        return {"state": "bootstrap", "workflow_run_id": None}
    conclusion = latest.get("conclusion")
    if conclusion == "success":
        return {"state": "green", "workflow_run_id": latest["id"]}
    if allow_recovery:
        return {"state": "red-recovery", "workflow_run_id": latest["id"]}
    raise MainHealthError(
        "latest completed Main / Broad regression is red; automatic merges are "
        f"blocked by workflow run {latest['id']}. Apply {RECOVERY_LABEL!r} only "
        "to the high-risk fix-forward pull request."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Block automatic merges while completed main regression is red"
    )
    parser.add_argument("--runs-json", required=True)
    parser.add_argument("--event", required=True)
    args = parser.parse_args()
    try:
        runs = json.loads(Path(args.runs_json).read_text(encoding="utf-8"))
        event = json.loads(Path(args.event).read_text(encoding="utf-8"))
        result = verify_main_health(
            runs,
            allow_recovery=recovery_requested(event),
        )
    except (OSError, json.JSONDecodeError, MainHealthError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
