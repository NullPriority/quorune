from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorune.util import stable_json
from scripts.harvest_outcome_history import (
    _canonical_commit,
    _public_receipt,
    _receipt,
    _transition_metrics,
    HarvestOutcomeHistoryError,
    validated_semantic_transition_declaration,
)


PR_EVIDENCE_SCHEMA_VERSION = 1
DEFAULT_METADATA_PATH = "platform/rules-subsystems.json"
_COMPILER_VERSION = re.compile(r"^oracle-ir-v(?P<version>\d+)$")
_SEMANTIC_METADATA_FIELDS = frozenset(
    {
        "transition_id",
        "bundle_id",
        "candidate_ids",
        "family_ids",
        "capability_ids",
        "expected_complete_card_gain",
        "measurement_id",
        "non_harvest_reason",
    }
)
_PRODUCTION_PREFIXES = ("quorune/", "server/", "web/src/")


class PullRequestEvidenceError(ValueError):
    pass


def _git(root: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PullRequestEvidenceError(
            f"Git evidence command failed: git {' '.join(args)}"
        ) from exc


def _git_json(root: Path, commit: str, path: str) -> dict[str, Any]:
    try:
        value = json.loads(_git(root, "show", f"{commit}:{path}"))
    except json.JSONDecodeError as exc:
        raise PullRequestEvidenceError(
            f"PR evidence input is not valid JSON: {path} at {commit}"
        ) from exc
    if not isinstance(value, dict):
        raise PullRequestEvidenceError(
            f"PR evidence input must be an object: {path} at {commit}"
        )
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise PullRequestEvidenceError(f"{label} must be a nonnegative integer")
    return value


def _sorted_ids(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise PullRequestEvidenceError(f"{label} must be an array")
    result = [str(item) for item in value]
    if result != sorted(set(result)) or any(not item for item in result):
        raise PullRequestEvidenceError(f"{label} must be sorted and unique")
    return result


def semantic_evidence_metadata(
    catalog: Mapping[str, Any],
    *,
    previous_catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    work_selection = catalog.get("work_selection")
    if not isinstance(work_selection, Mapping):
        raise PullRequestEvidenceError(
            "Rules subsystem catalog lacks work-selection metadata"
        )
    declaration = work_selection.get("semantic_transition_declaration")
    if previous_catalog is not None:
        previous_selection = previous_catalog.get("work_selection")
        if not isinstance(previous_selection, Mapping):
            raise PullRequestEvidenceError(
                "Base rules subsystem catalog lacks work-selection metadata"
            )
        if declaration == previous_selection.get(
            "semantic_transition_declaration"
        ):
            declaration = None
    if declaration is None:
        return {
            "transition_id": None,
            "bundle_id": None,
            "candidate_ids": [],
            "family_ids": [],
            "capability_ids": [],
            "expected_complete_card_gain": None,
            "measurement_id": None,
            "non_harvest_reason": (
                "No semantic support transition is declared at the exact head."
            ),
        }
    try:
        result = validated_semantic_transition_declaration(declaration)
    except HarvestOutcomeHistoryError as exc:
        raise PullRequestEvidenceError(
            "Semantic PR evidence metadata is invalid"
        ) from exc
    result.pop("outcome_kind")
    result.pop("compiler_version")
    result.setdefault("expected_complete_card_gain", None)
    result.setdefault("measurement_id", None)
    return result


def _version_delta(base: str, head: str) -> int | None:
    base_match = _COMPILER_VERSION.fullmatch(base)
    head_match = _COMPILER_VERSION.fullmatch(head)
    if base_match is None or head_match is None:
        return None
    return int(head_match["version"]) - int(base_match["version"])


def _baseline_metrics(value: Mapping[str, Any]) -> dict[str, int]:
    try:
        metrics = {
            "commander_engine_logical_lines": value["engine"]["logical_lines"],
            "direct_game_state_writes": len(
                value["direct_game_state_write_identities"]
            ),
            "prohibited_runtime_oracle_text_accesses": len(
                value["runtime_oracle_text_access_identities"]
            ),
            "printed_name_helpers": len(value["card_named_helpers"]),
            "oracle_id_literals": len(value["oracle_id_literals"]),
        }
    except (KeyError, TypeError) as exc:
        raise PullRequestEvidenceError(
            "Architecture guard baseline lacks required PR evidence"
        ) from exc
    return {
        key: _nonnegative_int(count, f"architecture baseline {key}")
        for key, count in metrics.items()
    }


def _delta(base: Mapping[str, int], head: Mapping[str, int]) -> dict[str, int]:
    return {
        key: int(head.get(key, 0)) - int(base.get(key, 0))
        for key in sorted(set(base) | set(head))
    }


def _generated_paths(manifest: Mapping[str, Any]) -> set[str]:
    generators = manifest.get("generators")
    if not isinstance(generators, list):
        raise PullRequestEvidenceError(
            "Generated-artifact manifest lacks its generator inventory"
        )
    result: set[str] = set()
    for row in generators:
        if not isinstance(row, Mapping) or not isinstance(row.get("outputs"), list):
            raise PullRequestEvidenceError(
                "Generated-artifact manifest has an invalid generator row"
            )
        result.update(str(path) for path in row["outputs"])
    return result


def _line_changes(
    root: Path,
    base_commit: str,
    head_commit: str,
    generated_paths: set[str],
) -> dict[str, dict[str, int]]:
    result = {
        category: {
            "additions": 0,
            "deletions": 0,
            "net": 0,
            "text_files_changed": 0,
            "binary_files_changed": 0,
        }
        for category in ("production", "test", "generated")
    }
    raw = _git(
        root,
        "diff",
        "--numstat",
        "--no-renames",
        base_commit,
        head_commit,
        "--",
    ).decode("utf-8", errors="strict")
    for line in raw.splitlines():
        fields = line.split("\t", 2)
        if len(fields) != 3:
            raise PullRequestEvidenceError("Git returned malformed line evidence")
        added, deleted, path = fields
        normalized = path.replace("\\", "/")
        category = None
        if normalized in generated_paths:
            category = "generated"
        elif normalized.startswith("tests/"):
            category = "test"
        elif normalized == "simctl.py" or normalized.startswith(
            _PRODUCTION_PREFIXES
        ):
            category = "production"
        if category is None:
            continue
        if added == "-" or deleted == "-":
            result[category]["binary_files_changed"] += 1
            continue
        additions = int(added)
        deletions = int(deleted)
        result[category]["additions"] += additions
        result[category]["deletions"] += deletions
        result[category]["net"] += additions - deletions
        result[category]["text_files_changed"] += 1
    return result


def _printed_name_count(audit: Mapping[str, Any]) -> int:
    architecture = audit.get("architecture")
    if not isinstance(architecture, Mapping):
        raise PullRequestEvidenceError("Architecture audit is malformed")
    helpers = architecture.get("card_named_helpers")
    if not isinstance(helpers, list):
        raise PullRequestEvidenceError(
            "Architecture audit lacks printed-name helper evidence"
        )
    return len(helpers)


def build_pr_evidence(
    root: str | Path,
    *,
    base_revision: str,
    head_revision: str,
    metadata: Mapping[str, Any] | None = None,
    metadata_path: str = DEFAULT_METADATA_PATH,
) -> dict[str, Any]:
    repository = Path(root).resolve()
    try:
        base_commit = _canonical_commit(
            repository, base_revision, "base_revision"
        )
        head_commit = _canonical_commit(
            repository, head_revision, "head_revision"
        )
    except HarvestOutcomeHistoryError as exc:
        raise PullRequestEvidenceError("PR evidence revisions are invalid") from exc
    if base_commit == head_commit:
        raise PullRequestEvidenceError("PR evidence requires distinct base and head")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", base_commit, head_commit],
        cwd=repository,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode:
        raise PullRequestEvidenceError("PR base must be an ancestor of its head")

    if metadata is None:
        base_catalog = _git_json(repository, base_commit, metadata_path)
        head_catalog = _git_json(repository, head_commit, metadata_path)
        source_metadata = semantic_evidence_metadata(
            head_catalog,
            previous_catalog=base_catalog,
        )
    else:
        try:
            validated = validated_semantic_transition_declaration(metadata)
        except HarvestOutcomeHistoryError as exc:
            raise PullRequestEvidenceError(
                "PR evidence source metadata is invalid"
            ) from exc
        validated.pop("outcome_kind")
        validated.pop("compiler_version")
        source_metadata = validated
        source_metadata.setdefault("expected_complete_card_gain", None)
        source_metadata.setdefault("measurement_id", None)
    if set(source_metadata) != _SEMANTIC_METADATA_FIELDS:
        raise PullRequestEvidenceError("PR evidence source metadata is incomplete")
    candidate_ids = _sorted_ids(source_metadata["candidate_ids"], "candidate_ids")
    family_ids = _sorted_ids(source_metadata["family_ids"], "family_ids")
    capability_ids = _sorted_ids(
        source_metadata["capability_ids"], "capability_ids"
    )

    try:
        base_receipt = _receipt(repository, base_commit)
        head_receipt = _receipt(repository, head_commit)
        transition = _transition_metrics(base_receipt, head_receipt)
    except HarvestOutcomeHistoryError as exc:
        raise PullRequestEvidenceError(
            "PR evidence corpus receipts are invalid"
        ) from exc
    base_audit = _git_json(
        repository, base_commit, "coverage/architecture-audit.json"
    )
    head_audit = _git_json(
        repository, head_commit, "coverage/architecture-audit.json"
    )
    base_baseline = _baseline_metrics(
        _git_json(
            repository,
            base_commit,
            "platform/architecture-guard-baseline.json",
        )
    )
    head_baseline = _baseline_metrics(
        _git_json(
            repository,
            head_commit,
            "platform/architecture-guard-baseline.json",
        )
    )
    base_manifest = _git_json(
        repository, base_commit, "platform/generated-artifacts.json"
    )
    head_manifest = _git_json(
        repository, head_commit, "platform/generated-artifacts.json"
    )
    generated_paths = _generated_paths(base_manifest) | _generated_paths(
        head_manifest
    )

    oracle_delta = transition["oracle_status_delta"]
    program_delta = transition["card_program_status_delta"]
    base_architecture = dict(base_receipt["architecture"])
    head_architecture = dict(head_receipt["architecture"])
    base_architecture["printed_name_helpers"] = _printed_name_count(base_audit)
    head_architecture["printed_name_helpers"] = _printed_name_count(head_audit)
    actual_architecture = _delta(base_architecture, head_architecture)
    actual_architecture["printed_name_helpers"] = (
        _printed_name_count(head_audit) - _printed_name_count(base_audit)
    )
    high_risk_base = int(
        base_receipt["interaction_assurance"].get(
            "applicable_high_risk_pairs", 0
        )
    )
    high_risk_head = int(
        head_receipt["interaction_assurance"].get(
            "applicable_high_risk_pairs", 0
        )
    )
    covered_base = int(
        base_receipt["interaction_assurance"].get("covered_high_risk_pairs", 0)
    )
    covered_head = int(
        head_receipt["interaction_assurance"].get("covered_high_risk_pairs", 0)
    )

    payload: dict[str, Any] = {
        "schema_version": PR_EVIDENCE_SCHEMA_VERSION,
        "base_sha": base_commit,
        "exact_head_sha": head_commit,
        "source_metadata": {
            **source_metadata,
            "candidate_ids": candidate_ids,
            "family_ids": family_ids,
            "capability_ids": capability_ids,
            "source_path": metadata_path,
        },
        "versions": {
            "compiler": {
                "base": base_receipt["compiler_version"],
                "head": head_receipt["compiler_version"],
                "numeric_delta": _version_delta(
                    str(base_receipt["compiler_version"]),
                    str(head_receipt["compiler_version"]),
                ),
            },
            "card_program_schema": {
                "base": base_receipt["card_program_schema_version"],
                "head": head_receipt["card_program_schema_version"],
                "delta": (
                    head_receipt["card_program_schema_version"]
                    - base_receipt["card_program_schema_version"]
                ),
            },
        },
        "cards": {
            "oracle_exact": oracle_delta.get("exact", 0),
            "trusted": transition["actual_trusted_card_gain"],
            "capability_closed": transition[
                "actual_capability_closed_card_gain"
            ],
            "oracle_partial": oracle_delta.get("partial", 0),
            "oracle_unresolved": oracle_delta.get("unresolved", 0),
            "card_program_residual": program_delta.get("residual", 0),
            "failed": transition["failed_card_delta"],
            "hard_construction_failures": transition[
                "hard_construction_failure_delta"
            ],
        },
        "abilities": {
            "oracle_exact_node_delta": transition[
                "oracle_exact_ability_node_delta"
            ],
            "card_program_record_delta": transition[
                "card_program_ability_record_delta"
            ],
            "executable_trust_transition_delta": transition[
                "executable_trust_transition_delta"
            ],
            "executable_trust_transitions": transition[
                "executable_trust_transitions"
            ],
            "frontier_structural_carrier_delta": transition[
                "frontier_ability_carrier_delta"
            ],
            "card_program_structural_reconciliation": transition[
                "card_program_structural_carrier_reconciliation"
            ],
        },
        "material_residuals": {
            "oracle_reduction": transition[
                "actual_material_oracle_residual_reduction"
            ],
            "card_program_reduction": transition[
                "actual_material_card_program_residual_reduction"
            ],
        },
        "interaction_coverage": {
            "delta": transition["interaction_assurance_delta"],
            "uncovered_high_risk_pairs": {
                "base": high_risk_base - covered_base,
                "head": high_risk_head - covered_head,
                "delta": (
                    (high_risk_head - covered_head)
                    - (high_risk_base - covered_base)
                ),
            },
        },
        "architecture": {
            "actual_pr_source": {
                "base": base_architecture,
                "head": head_architecture,
                "delta": actual_architecture,
            },
            "reviewed_baseline": {
                "base": base_baseline,
                "head": head_baseline,
                "delta": _delta(base_baseline, head_baseline),
                "separate_from_actual_pr_source_delta": True,
            },
        },
        "lines": {
            **_line_changes(
                repository,
                base_commit,
                head_commit,
                generated_paths,
            ),
            "production_logical_line_delta": actual_architecture[
                "production_logical_lines"
            ],
        },
        "receipt_identity": {
            "base": _public_receipt(base_receipt)["blobs"],
            "head": _public_receipt(head_receipt)["blobs"],
        },
    }
    payload["fingerprint"] = hashlib.sha256(
        stable_json(payload).encode("utf-8")
    ).hexdigest()
    return payload


def _signed(value: int | None) -> str:
    if value is None:
        return "unknown"
    return f"{value:+d}"


def _change(base: Any, head: Any, delta: int | None) -> str:
    return f"`{base}` -> `{head}` (delta `{_signed(delta)}`)"


def _ids(values: Sequence[str], reason: str) -> str:
    if values:
        return ", ".join(f"`{value}`" for value in values)
    return f"N/A — {reason}"


def pr_evidence_markdown_fields(evidence: Mapping[str, Any]) -> dict[str, str]:
    metadata = evidence["source_metadata"]
    versions = evidence["versions"]
    cards = evidence["cards"]
    abilities = evidence["abilities"]
    residuals = evidence["material_residuals"]
    architecture = evidence["architecture"]
    actual_source = architecture["actual_pr_source"]
    actual = actual_source["delta"]
    baseline = architecture["reviewed_baseline"]
    lines = evidence["lines"]
    reason = str(
        metadata.get("non_harvest_reason")
        or "The declared transition does not represent this identity kind."
    )
    compiler = versions["compiler"]
    schema = versions["card_program_schema"]
    transitions = abilities["executable_trust_transitions"]
    carriers = abilities["frontier_structural_carrier_delta"]
    reconciliation = abilities["card_program_structural_reconciliation"]
    interaction = evidence["interaction_coverage"]
    high_risk = interaction["uncovered_high_risk_pairs"]

    def line_value(name: str) -> str:
        value = lines[name]
        return (
            f"additions `{value['additions']}`, deletions `{value['deletions']}`, "
            f"net `{_signed(value['net'])}`, binary files "
            f"`{value['binary_files_changed']}`"
        )

    return {
        "Represented family IDs": _ids(metadata["family_ids"], reason),
        "Represented capability IDs": _ids(
            metadata["capability_ids"], reason
        ),
        "Exact head SHA": f"`{evidence['exact_head_sha']}`",
        "Compiler version delta": _change(
            compiler["base"], compiler["head"], compiler["numeric_delta"]
        ),
        "CardProgram schema delta": _change(
            schema["base"], schema["head"], schema["delta"]
        ),
        "Exact, trusted, and capability-closed card delta": (
            f"Oracle exact `{_signed(cards['oracle_exact'])}`; trusted "
            f"`{_signed(cards['trusted'])}`; capability-closed "
            f"`{_signed(cards['capability_closed'])}`"
        ),
        "Partial, unresolved, and failed card delta": (
            f"Oracle partial `{_signed(cards['oracle_partial'])}`; Oracle "
            f"unresolved `{_signed(cards['oracle_unresolved'])}`; CardProgram "
            f"residual `{_signed(cards['card_program_residual'])}`; failed "
            f"`{_signed(cards['failed'])}`; hard construction "
            f"`{_signed(cards['hard_construction_failures'])}`"
        ),
        "Oracle and CardProgram ability delta": (
            f"Oracle exact nodes "
            f"`{_signed(abilities['oracle_exact_node_delta'])}`; net "
            f"CardProgram records "
            f"`{_signed(abilities['card_program_record_delta'])}`; explicit "
            f"balance `{_signed(reconciliation['unresolved_structural_balance'])}`"
        ),
        "Executable trust transitions": (
            f"promoted `{transitions['promoted']}`; regressed "
            f"`{transitions['regressed']}`; net "
            f"`{_signed(abilities['executable_trust_transition_delta'])}`"
        ),
        "Structural carrier delta and reconciliation": (
            f"frontier additions `{carriers['additions']}`, removals "
            f"`{carriers['removals']}`, reclassifications "
            f"`{carriers['reclassifications']}`; CardProgram additions, "
            "removals, and reclassifications `unknown` from aggregate-only "
            f"receipts; net records "
            f"`{_signed(reconciliation['net_ability_record_delta'])}` versus "
            f"Oracle exact nodes "
            f"`{_signed(reconciliation['oracle_exact_ability_node_delta'])}`"
        ),
        "Oracle and CardProgram material residual delta": (
            f"Oracle reduction `{_signed(residuals['oracle_reduction'])}`; "
            f"CardProgram reduction "
            f"`{_signed(residuals['card_program_reduction'])}`"
        ),
        "Interaction coverage delta": (
            f"covered high-risk "
            f"`{_signed(interaction['delta'].get('covered_high_risk_pairs', 0))}`; "
            f"applicable high-risk "
            f"`{_signed(interaction['delta'].get('applicable_high_risk_pairs', 0))}`; "
            f"uncovered `{high_risk['base']}` -> `{high_risk['head']}` "
            f"(delta `{_signed(high_risk['delta'])}`)"
        ),
        "Actual CommanderEngine line delta": _change(
            actual_source["base"]["commander_engine_logical_lines"],
            actual_source["head"]["commander_engine_logical_lines"],
            int(actual["commander_engine_logical_lines"]),
        ),
        "Reviewed architecture-baseline delta": _change(
            baseline["base"]["commander_engine_logical_lines"],
            baseline["head"]["commander_engine_logical_lines"],
            baseline["delta"]["commander_engine_logical_lines"],
        ),
        "Direct authoritative-write delta": _signed(
            actual["direct_game_state_writes"]
        ),
        "Runtime-text delta": _signed(
            actual["prohibited_runtime_oracle_text_accesses"]
        ),
        "Printed-name and Oracle-ID delta": (
            f"printed-name helpers `{_signed(actual['printed_name_helpers'])}`; "
            f"Oracle-ID literals `{_signed(actual['oracle_id_literals'])}`"
        ),
        "Production, test, and generated line delta": (
            f"production logical `{_signed(lines['production_logical_line_delta'])}`; "
            f"production diff {line_value('production')}; test diff "
            f"{line_value('test')}; generated diff {line_value('generated')}"
        ),
        "Evidence fingerprint": f"`{evidence['fingerprint']}`",
        "Evidence command": (
            "`.\\.venv\\Scripts\\python.exe scripts\\pr_evidence.py "
            f"--base {evidence['base_sha']} --head {evidence['exact_head_sha']} "
            "--format markdown`"
        ),
    }


def render_pr_evidence_markdown(evidence: Mapping[str, Any]) -> str:
    fields = pr_evidence_markdown_fields(evidence)
    return "\n".join(
        [
            "## Generated base/head evidence",
            "",
            *(f"- {label}: {value}" for label, value in fields.items()),
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate reconciled substantive pull-request evidence from "
            "immutable base/head receipts"
        )
    )
    parser.add_argument("--base", default=os.environ.get("PR_BASE_SHA", "origin/main"))
    parser.add_argument("--head", default=os.environ.get("PR_HEAD_SHA", "HEAD"))
    parser.add_argument("--metadata", default=DEFAULT_METADATA_PATH)
    parser.add_argument(
        "--format", choices=("json", "markdown", "both"), default="both"
    )
    parser.add_argument("--json-output")
    parser.add_argument("--markdown-output")
    args = parser.parse_args()
    try:
        evidence = build_pr_evidence(
            ROOT,
            base_revision=args.base,
            head_revision=args.head,
            metadata_path=args.metadata,
        )
        json_text = stable_json(evidence) + "\n"
        markdown_text = render_pr_evidence_markdown(evidence)
        if args.json_output:
            Path(args.json_output).write_text(
                json_text, encoding="utf-8", newline="\n"
            )
        if args.markdown_output:
            Path(args.markdown_output).write_text(
                markdown_text, encoding="utf-8", newline="\n"
            )
        if args.format in {"json", "both"} and not args.json_output:
            print(json_text, end="")
        if args.format == "both" and not args.json_output and not args.markdown_output:
            print("---")
        if args.format in {"markdown", "both"} and not args.markdown_output:
            print(markdown_text, end="")
    except (OSError, PullRequestEvidenceError) as exc:
        print(f"PR evidence generation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
