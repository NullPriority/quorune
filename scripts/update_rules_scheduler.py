from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorune.rules_scheduler import (
    build_rules_dependency_queue_from_root,
)
from quorune.util import stable_json
from quorune.work_selection import selected_work_candidate
from scripts.harvest_outcome_history import build_harvest_outcome_history


JSON_OUTPUT = ROOT / "coverage" / "rules-dependency-queue.json"
MARKDOWN_OUTPUT = ROOT / "docs" / "RULES_DEPENDENCY_QUEUE.md"
HARVEST_HISTORY_OUTPUT = ROOT / "coverage" / "harvest-outcome-history.json"


def _json_text(value: Mapping[str, Any]) -> str:
    return stable_json(value) + "\n"


def _compact_markdown(value: Mapping[str, Any]) -> str:
    summary = value["summary"]
    selected = value["selected_batch"]
    work = value["work_selection"]
    selected_work = selected_work_candidate(work)
    selected_work_id = (
        str(selected_work["candidate_id"])
        if selected_work is not None
        else "none"
    )
    selected_work_class = (
        str(selected_work["candidate_class"])
        if selected_work is not None
        else "none"
    )
    selected_work_state = (
        str(selected_work["work_state"])
        if selected_work is not None
        else "none"
    )
    selected_gameplay_trust = (
        str(
            bool(
                (selected_work.get("measurement_task") or {}).get(
                    "grants_gameplay_trust"
                )
            )
        ).lower()
        if selected_work_state == "cohort_measurement"
        else "not_applicable"
    )
    selected_reason = (
        str(selected_work["reranking_reason"])
        if selected_work is not None
        else (
            "No serious candidate currently meets the generated eligibility "
            "policy; retain visible deferred pressure and recompute after the "
            "next measured frontier classification."
        )
    )
    command = r".\.venv\Scripts\python.exe scripts\update_rules_scheduler.py --write"
    fingerprint = hashlib.sha256(_json_text(value).encode("utf-8")).hexdigest()
    blockers = list(selected["exit_criteria"])
    if not blockers:
        blockers = ["No selected-batch blockers were reported."]
    lines = [
        "---",
        'title: "Rules dependency queue"',
        'status: "generated"',
        'authoritative_source: "coverage/rules-dependency-queue.json"',
        f'verified: "{fingerprint}"',
        'audience: "rules, compiler, and engine contributors"',
        'maintenance: "generated"',
        'generated_source: "coverage/rules-dependency-queue.json"',
        f'generation_command: "{command}"',
        "---",
        "",
        "# Rules dependency queue",
        "",
        f"Source fingerprint: `{value['fingerprint']}`",
        "",
        "## Current top-level state",
        "",
        f"- Pinned rules: `{summary['total_rules']}`",
        f"- Queued rules: `{summary['queued_rules']}`",
        f"- Subsystems: `{summary['subsystem_count']}`",
        f"- Selected subsystem: `{selected['subsystem_id']}`",
        f"- Selected batch: `{selected['batch_id']}`",
        f"- Selected cross-program work: `{selected_work_id}`",
        f"- Selected work class: `{selected_work_class}`",
        f"- Selected work state: `{selected_work_state}`",
        f"- Measurement grants gameplay trust: `{selected_gameplay_trust}`",
        "",
        "## Cross-program work selection",
        "",
        "The rules batch remains dependency-ready, but final foreground work is "
        "reranked with deterministic CI, replay/privacy, architecture, runtime-text, "
        "interaction-assurance, compiler, and card-frontier evidence. A larger card "
        "gain cannot outrank a higher-priority correctness class.",
        "When no implementation candidate is eligible, one bounded cohort measurement "
        "may be selected. Its upper bounds remain non-executable and grant no gameplay "
        "trust until the declared upgrade evidence is generated.",
        "",
        "Priority classes: "
        + " → ".join(f"`{item}`" for item in work["priority_classes"]),
        "",
        "| Rank | Selection | Work state | Implementation eligible | Candidate | Class | Members | Contexts | Complete cards | Residuals | Cards/hour | Runtime text | Direct writes |",
        "|---:|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
        *(
            "| "
            + " | ".join(
                [
                    str(candidate["rank"]),
                    str(candidate["selection_state"]),
                    str(candidate["work_state"]),
                    str(candidate["implementation_eligible"]).lower(),
                    f"`{candidate['candidate_id']}`",
                    f"`{candidate['candidate_class']}`",
                    str(len(candidate["bundle"]["member_family_ids"])),
                    str(len(candidate["bundle"]["source_contexts"])),
                    (
                        str(candidate["expected_complete_card_gain"])
                        if candidate["expected_complete_card_gain"] is not None
                        else "unknown"
                    ),
                    (
                        str(candidate["expected_material_residual_reduction"])
                        if candidate["expected_material_residual_reduction"] is not None
                        else "unknown"
                    ),
                    (
                        str(
                            candidate["bundle"].get(
                                "predicted_complete_cards_per_cycle_hour"
                            )
                        )
                        if candidate["bundle"].get(
                            "predicted_complete_cards_per_cycle_hour"
                        )
                        is not None
                        else "unknown"
                    ),
                    (
                        str(
                            candidate["runtime_oracle_text_removal"].get(
                                "expected_count"
                            )
                        )
                        if candidate["runtime_oracle_text_removal"].get(
                            "expected_count"
                        )
                        is not None
                        else "unknown"
                    ),
                    (
                        str(candidate["direct_write_migration"].get("expected_count"))
                        if candidate["direct_write_migration"].get("expected_count")
                        is not None
                        else "unknown"
                    ),
                ]
            )
            + " |"
            for candidate in work["candidates"]
        ),
        "",
        f"Selected reason: {selected_reason}",
        "",
        "## Top blockers",
        "",
        *(f"- {item}" for item in blockers[:5]),
        "",
        "Complete rule, subsystem, dependency, classification, and selected-batch data "
        "plus complete readiness, blocker-card, architecture, interaction, and reranking "
        "fields for every serious candidate are in the "
        "[machine-readable rules queue](../coverage/rules-dependency-queue.json).",
        "",
        "Exact generation command:",
        "",
        "```powershell",
        command,
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    catalog = json.loads(
        (ROOT / "platform" / "rules-subsystems.json").read_text(
            encoding="utf-8"
        )
    )
    work_policy = catalog.get("work_selection") or {}
    harvest_history = build_harvest_outcome_history(
        ROOT,
        work_policy.get("harvest_provenance"),
        work_policy.get("semantic_transition_declaration"),
    )
    value = build_rules_dependency_queue_from_root(
        ROOT, harvest_outcome_history=harvest_history
    )
    expected_history = _json_text(harvest_history)
    expected_json = _json_text(value)
    expected_markdown = _compact_markdown(value)
    if args.write:
        HARVEST_HISTORY_OUTPUT.write_text(
            expected_history, encoding="utf-8", newline="\n"
        )
        JSON_OUTPUT.write_text(
            expected_json, encoding="utf-8", newline="\n"
        )
        MARKDOWN_OUTPUT.write_text(
            expected_markdown, encoding="utf-8", newline="\n"
        )
        return 0
    stale = []
    for path, expected in (
        (HARVEST_HISTORY_OUTPUT, expected_history),
        (JSON_OUTPUT, expected_json),
        (MARKDOWN_OUTPUT, expected_markdown),
    ):
        actual = path.read_text(encoding="utf-8") if path.exists() else ""
        if actual != expected:
            stale.append(path.relative_to(ROOT).as_posix())
    if stale:
        print(
            "Rules scheduler outputs are stale; run "
            "python scripts/update_rules_scheduler.py --write: "
            + ", ".join(stale),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
