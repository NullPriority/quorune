from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping, Sequence

from quorune.util import stable_json


HARVEST_HISTORY_SCHEMA_VERSION = 3
HARVEST_HISTORY_ALGORITHM_VERSION = "semantic-content-fixed-point-v7"
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_PROGRAM_PATH = "coverage/card-program-coverage-commander.json"
_ORACLE_PATH = "coverage/oracle-coverage-commander.json"
_FRONTIER_PATH = "coverage/card-unlock-frontier.json.gz"
_INTERACTIONS_PATH = "coverage/reusable-piece-interactions.json.gz"
_ARCHITECTURE_PATH = "coverage/architecture-audit.json"
_COHORT_MEASUREMENTS_PATH = (
    "coverage/work-selection-cohort-measurements.json"
)
_CARD_DATA_SNAPSHOT_FIELDS = (
    "schema_version",
    "card_count",
    "ruling_count",
    "oracle_source_sha256",
    "rulings_source_sha256",
    "scryfall_oracle_updated_at",
    "scryfall_rulings_updated_at",
)


class HarvestOutcomeHistoryError(ValueError):
    pass


def _hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def _semantic_report_sha256(
    path: str, raw: bytes, value: Mapping[str, Any]
) -> str:
    del raw
    canonical = dict(value)
    if path == _FRONTIER_PATH:
        canonical.pop("fingerprint", None)
        snapshot = value.get("card_data_snapshot")
        if not isinstance(snapshot, Mapping):
            raise HarvestOutcomeHistoryError(
                "Card-unlock frontier lacks a content-bound card snapshot"
            )
        canonical["card_data_snapshot"] = {
            key: snapshot[key]
            for key in _CARD_DATA_SNAPSHOT_FIELDS
            if snapshot.get(key) is not None
        }
    return _hash(
        {
            "schema_version": 1,
            "report": path,
            "semantic_content": canonical,
        }
    )


def _git(
    root: Path,
    *args: str,
    check: bool = True,
    input_bytes: bytes | None = None,
) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            input=input_bytes,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise HarvestOutcomeHistoryError(
            "Unable to read immutable harvest provenance from Git"
        ) from exc
    return completed.stdout


def _canonical_commit(root: Path, value: Any, label: str) -> str:
    commit = str(value or "")
    if not _COMMIT.fullmatch(commit):
        raise HarvestOutcomeHistoryError(f"{label} must be a full Git commit")
    resolved = _git(root, "rev-parse", f"{commit}^{{commit}}").decode().strip()
    if resolved != commit:
        raise HarvestOutcomeHistoryError(f"{label} is not canonical")
    return commit


def _durable_main_tip(root: Path) -> str:
    """Resolve the landed main line used by immutable harvest provenance."""

    for reference in (
        "refs/remotes/origin/HEAD",
        "refs/remotes/origin/main",
        "refs/heads/main",
    ):
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", f"{reference}^{{commit}}"],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if completed.returncode == 0:
            resolved = completed.stdout.decode().strip()
            if _COMMIT.fullmatch(resolved):
                return resolved
    raise HarvestOutcomeHistoryError(
        "Unable to resolve the durable main line for harvest provenance"
    )


def _require_landed_harvest_head(root: Path, head_commit: str) -> None:
    """Reject feature-only receipt commits that a squash merge can discard."""

    durable_tip = _durable_main_tip(root)
    landed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", head_commit, durable_tip],
        cwd=root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if landed.returncode != 0:
        raise HarvestOutcomeHistoryError(
            "Harvest head must be landed on the durable main line; keep the "
            "semantic transition declaration pending until squash merge"
        )


def _blobs(
    root: Path,
    commit: str,
    paths: Sequence[str],
) -> dict[str, tuple[str, bytes]]:
    requests = tuple(f"{commit}:{path}" for path in paths)
    raw = _git(
        root,
        "cat-file",
        "--batch",
        input_bytes=("\n".join(requests) + "\n").encode("utf-8"),
    )
    cursor = 0
    result: dict[str, tuple[str, bytes]] = {}
    for path in paths:
        header_end = raw.find(b"\n", cursor)
        if header_end < 0:
            raise HarvestOutcomeHistoryError("Git returned an incomplete blob batch")
        header = raw[cursor:header_end].decode("ascii", errors="strict").split()
        if len(header) != 3 or header[1] != "blob" or not _COMMIT.fullmatch(
            header[0]
        ):
            raise HarvestOutcomeHistoryError(
                f"Git returned an invalid harvest blob for {path}"
            )
        size = int(header[2])
        content_start = header_end + 1
        content_end = content_start + size
        if raw[content_end : content_end + 1] != b"\n":
            raise HarvestOutcomeHistoryError("Git returned an incomplete blob body")
        result[path] = (header[0], raw[content_start:content_end])
        cursor = content_end + 1
    if cursor != len(raw):
        raise HarvestOutcomeHistoryError("Git returned unexpected harvest blob data")
    return result


def _json_object(raw: bytes, label: str, *, compressed: bool = False) -> dict:
    try:
        payload = gzip.decompress(raw) if compressed else raw
        value = json.loads(payload)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HarvestOutcomeHistoryError(f"Invalid {label}") from exc
    if not isinstance(value, dict):
        raise HarvestOutcomeHistoryError(f"{label} must be an object")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise HarvestOutcomeHistoryError(f"{label} must be a nonnegative integer")
    return value


def _status_counts(value: Any, label: str) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise HarvestOutcomeHistoryError(f"{label} must be an object")
    result: dict[str, int] = {}
    for key, count in value.items():
        result[str(key)] = _nonnegative_int(count, f"{label}.{key}")
    return result


def _architecture_metrics(value: Mapping[str, Any]) -> dict[str, int]:
    architecture = value.get("architecture")
    if not isinstance(architecture, Mapping):
        raise HarvestOutcomeHistoryError("Architecture receipt is malformed")
    try:
        metrics = {
            "commander_engine_logical_lines": architecture["engine"][
                "logical_lines"
            ],
            "direct_game_state_writes": architecture[
                "direct_game_state_write_heuristic"
            ]["count"],
            "unowned_direct_game_state_writes": architecture[
                "direct_game_state_write_ownership"
            ]["unowned_writes"],
            "prohibited_runtime_oracle_text_accesses": architecture[
                "runtime_oracle_text_access"
            ]["prohibited_runtime_interpretation_count"],
            "prohibited_identity_dispatch_count": architecture[
                "card_identity_flow"
            ]["counts"]["prohibited_identity_dispatch_count"],
            "oracle_id_literals": architecture["oracle_id_literals"]["count"],
            "production_logical_lines": architecture["production"][
                "logical_lines"
            ],
        }
    except (KeyError, TypeError) as exc:
        raise HarvestOutcomeHistoryError(
            "Architecture receipt lacks required metrics"
        ) from exc
    return {
        key: _nonnegative_int(count, f"architecture.{key}")
        for key, count in metrics.items()
    }


def _receipt_from_reports(
    *,
    commit: str,
    reports: Mapping[str, tuple[str, bytes, dict[str, Any]]],
) -> dict[str, Any]:
    program = reports[_PROGRAM_PATH][2]
    oracle = reports[_ORACLE_PATH][2]
    frontier = reports[_FRONTIER_PATH][2]
    interactions = reports[_INTERACTIONS_PATH][2]
    architecture = reports[_ARCHITECTURE_PATH][2]
    program_snapshot = program.get("card_data_snapshot")
    oracle_snapshot = oracle.get("card_data_snapshot")
    frontier_snapshot = frontier.get("card_data_snapshot")
    identity_fields = {
        "schema_version",
        "card_count",
        "oracle_source_sha256",
        "rulings_source_sha256",
        "scryfall_oracle_updated_at",
        "scryfall_rulings_updated_at",
    }
    program_identity = {
        field: program_snapshot.get(field) for field in identity_fields
    } if isinstance(program_snapshot, Mapping) else None
    oracle_identity = {
        field: oracle_snapshot.get(field) for field in identity_fields
    } if isinstance(oracle_snapshot, Mapping) else None
    frontier_identity = {
        field: frontier_snapshot.get(field) for field in identity_fields
    } if isinstance(frontier_snapshot, Mapping) else None
    if (
        not isinstance(program_snapshot, Mapping)
        or not isinstance(oracle_snapshot, Mapping)
        or not isinstance(frontier_snapshot, Mapping)
        or program_identity != oracle_identity
        or program_identity != frontier_identity
        or int(program.get("cards_considered") or -1)
        != int(frontier.get("cards_considered") or -2)
        or program.get("compiler_version") is None
        or not frontier.get("fingerprint")
    ):
        raise HarvestOutcomeHistoryError(
            f"Corpus receipts disagree at commit {commit}"
        )
    cards = frontier.get("cards")
    if not isinstance(cards, list):
        raise HarvestOutcomeHistoryError(
            f"Frontier receipt lacks complete cards at commit {commit}"
        )
    exact_abilities = 0
    card_states: dict[str, tuple[str, str, str]] = {}
    ability_carriers: dict[
        tuple[str, str, str], tuple[str, str | None, str, bool]
    ] = {}
    for card in cards:
        if not isinstance(card, Mapping):
            raise HarvestOutcomeHistoryError("Frontier card rows must be objects")
        oracle_id = str(card.get("oracle_id") or "")
        value = card.get("exact_ability_count")
        if not oracle_id or oracle_id in card_states:
            raise HarvestOutcomeHistoryError(
                "Frontier card identities must be unique and nonempty"
            )
        exact_abilities += _nonnegative_int(
            value, "frontier.exact_ability_count"
        )
        card_states[oracle_id] = (
            str(card.get("oracle_ir_status") or ""),
            str(card.get("card_program_status") or ""),
            str(card.get("card_program_trust_basis") or ""),
        )
        raw_abilities = card.get("abilities")
        if not isinstance(raw_abilities, list):
            raise HarvestOutcomeHistoryError(
                "Frontier card abilities must be an array"
            )
        for ability in raw_abilities:
            if not isinstance(ability, Mapping):
                raise HarvestOutcomeHistoryError(
                    "Frontier ability rows must be objects"
                )
            key = (
                oracle_id,
                str(ability.get("face_id") or ""),
                str(ability.get("ability_id") or ""),
            )
            if not all(key) or key in ability_carriers:
                raise HarvestOutcomeHistoryError(
                    "Frontier ability identities must be unique and complete"
                )
            ability_carriers[key] = (
                str(ability.get("status") or ""),
                (
                    str(ability["template_id"])
                    if ability.get("template_id") is not None
                    else None
                ),
                str(ability.get("kind") or ""),
                bool(ability.get("lowerable")),
            )
    program_statuses = _status_counts(
        program.get("status_counts"), "program.status_counts"
    )
    trust_basis = _status_counts(
        program.get("trust_basis_counts"), "program.trust_basis_counts"
    )
    oracle_statuses = _status_counts(
        oracle.get("status_counts"), "oracle.status_counts"
    )
    failures = program.get("failures")
    if not isinstance(failures, list):
        raise HarvestOutcomeHistoryError("Program failures must be an array")
    hard_failures = frontier.get("hard_construction_failures")
    if not isinstance(hard_failures, list):
        raise HarvestOutcomeHistoryError(
            "Frontier hard construction failures must be an array"
        )
    failed_programs = program_statuses.get("failed", len(failures))
    interaction_summary = _status_counts(
        interactions.get("summary"), "interactions.summary"
    )
    return {
        "commit": commit,
        "blobs": {
            path: {
                "git_blob_oid": oid,
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "semantic_sha256": _semantic_report_sha256(
                    path, raw, value
                ),
            }
            for path, (oid, raw, value) in sorted(reports.items())
        },
        "frontier_fingerprint": str(frontier["fingerprint"]),
        "compiler_version": str(program["compiler_version"]),
        "card_program_schema_version": int(
            program["card_program_schema_version"]
        ),
        "card_data_snapshot": {
            field: program_snapshot.get(field) for field in sorted(identity_fields)
        },
        "cards_considered": int(program["cards_considered"]),
        "oracle_status_counts": oracle_statuses,
        "card_program_status_counts": program_statuses,
        "card_program_trust_basis_counts": trust_basis,
        "trusted_programs": program_statuses.get("trusted", 0),
        "capability_closed_programs": trust_basis.get("capability_closed", 0),
        "failed_programs": _nonnegative_int(
            failed_programs, "program.failed_programs"
        ),
        "hard_construction_failures": len(hard_failures),
        "oracle_exact_ability_nodes": exact_abilities,
        "card_program_ability_records": _nonnegative_int(
            program.get("ability_programs"), "program.ability_programs"
        ),
        "oracle_material_residuals": _nonnegative_int(
            oracle.get("material_residuals"), "oracle.material_residuals"
        ),
        "card_program_material_residuals": _nonnegative_int(
            program.get("material_residuals"),
            "program.material_residuals",
        ),
        "interaction_assurance": interaction_summary,
        "architecture": _architecture_metrics(architecture),
        "_card_states": card_states,
        "_ability_carriers": ability_carriers,
    }


def _report_specs() -> tuple[tuple[str, bool], ...]:
    return (
        (_PROGRAM_PATH, False),
        (_ORACLE_PATH, False),
        (_FRONTIER_PATH, True),
        (_INTERACTIONS_PATH, True),
        (_ARCHITECTURE_PATH, False),
    )


def _receipt(root: Path, commit: str) -> dict[str, Any]:
    reports: dict[str, tuple[str, bytes, dict[str, Any]]] = {}
    report_specs = _report_specs()
    blobs = _blobs(root, commit, tuple(path for path, _compressed in report_specs))
    for path, compressed in report_specs:
        oid, raw = blobs[path]
        reports[path] = (
            oid,
            raw,
            _json_object(raw, path, compressed=compressed),
        )
    return _receipt_from_reports(commit=commit, reports=reports)


def _worktree_receipt(root: Path) -> dict[str, Any]:
    reports: dict[str, tuple[str, bytes, dict[str, Any]]] = {}
    for path, compressed in _report_specs():
        try:
            raw = (root / path).read_bytes()
        except OSError as exc:
            raise HarvestOutcomeHistoryError(
                f"Current harvest receipt is unavailable: {path}"
            ) from exc
        oid = _git(root, "hash-object", "--stdin", input_bytes=raw).decode().strip()
        if not _COMMIT.fullmatch(oid):
            raise HarvestOutcomeHistoryError(
                f"Current harvest blob identity is invalid: {path}"
            )
        reports[path] = (
            oid,
            raw,
            _json_object(raw, path, compressed=compressed),
        )
    receipt = _receipt_from_reports(commit="", reports=reports)
    receipt.pop("commit", None)
    return receipt


def _provenance_rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise HarvestOutcomeHistoryError("Harvest provenance must be an array")
    rows = list(value)
    if any(not isinstance(row, Mapping) for row in rows):
        raise HarvestOutcomeHistoryError("Harvest provenance rows must be objects")
    return rows


def _public_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in receipt.items()
        if not str(key).startswith("_")
    }


def _count_delta(
    base: Mapping[str, int],
    head: Mapping[str, int],
) -> dict[str, int]:
    return {
        key: head.get(key, 0) - base.get(key, 0)
        for key in sorted(set(base) | set(head))
    }


def _transition_metrics(
    base: Mapping[str, Any],
    head: Mapping[str, Any],
) -> dict[str, Any]:
    base_states = base["_card_states"]
    head_states = head["_card_states"]
    if not isinstance(base_states, Mapping) or not isinstance(head_states, Mapping):
        raise HarvestOutcomeHistoryError("Frontier card states are unavailable")
    transition_counts: dict[str, int] = {}
    promoted = 0
    regressed = 0
    for oracle_id in sorted(set(base_states) | set(head_states)):
        before = base_states.get(oracle_id)
        after = head_states.get(oracle_id)
        if before == after:
            continue
        label = f"{before!r}->{after!r}"
        transition_counts[label] = transition_counts.get(label, 0) + 1
        before_executable = before is not None and before[1] == "trusted"
        after_executable = after is not None and after[1] == "trusted"
        if after_executable and not before_executable:
            promoted += 1
        elif before_executable and not after_executable:
            regressed += 1

    base_carriers = base["_ability_carriers"]
    head_carriers = head["_ability_carriers"]
    if not isinstance(base_carriers, Mapping) or not isinstance(
        head_carriers, Mapping
    ):
        raise HarvestOutcomeHistoryError("Frontier ability carriers are unavailable")
    added_carriers = set(head_carriers) - set(base_carriers)
    removed_carriers = set(base_carriers) - set(head_carriers)
    reclassified_carriers = {
        key
        for key in set(base_carriers) & set(head_carriers)
        if base_carriers[key] != head_carriers[key]
    }

    oracle_status_delta = _count_delta(
        base["oracle_status_counts"], head["oracle_status_counts"]
    )
    program_status_delta = _count_delta(
        base["card_program_status_counts"],
        head["card_program_status_counts"],
    )
    exact_card_gain = oracle_status_delta.get("exact", 0)
    trusted_card_gain = (
        head["trusted_programs"] - base["trusted_programs"]
    )
    capability_closed_gain = (
        head["capability_closed_programs"]
        - base["capability_closed_programs"]
    )
    oracle_ability_delta = (
        head["oracle_exact_ability_nodes"]
        - base["oracle_exact_ability_nodes"]
    )
    program_ability_delta = (
        head["card_program_ability_records"]
        - base["card_program_ability_records"]
    )
    oracle_residual_reduction = (
        base["oracle_material_residuals"]
        - head["oracle_material_residuals"]
    )
    program_residual_reduction = (
        base["card_program_material_residuals"]
        - head["card_program_material_residuals"]
    )
    return {
        "actual_complete_card_gain": trusted_card_gain,
        "actual_exact_card_gain": exact_card_gain,
        "actual_trusted_card_gain": trusted_card_gain,
        "actual_capability_closed_card_gain": capability_closed_gain,
        "actual_exact_ability_gain": oracle_ability_delta,
        "actual_material_residual_reduction": program_residual_reduction,
        "actual_material_oracle_residual_reduction": (
            oracle_residual_reduction
        ),
        "actual_material_card_program_residual_reduction": (
            program_residual_reduction
        ),
        "oracle_status_delta": oracle_status_delta,
        "card_program_status_delta": program_status_delta,
        "failed_card_delta": head["failed_programs"] - base["failed_programs"],
        "hard_construction_failure_delta": (
            head["hard_construction_failures"]
            - base["hard_construction_failures"]
        ),
        "oracle_exact_ability_node_delta": oracle_ability_delta,
        "card_program_ability_record_delta": program_ability_delta,
        "executable_trust_transitions": {
            "promoted": promoted,
            "regressed": regressed,
            "by_transition": transition_counts,
        },
        "executable_trust_transition_delta": promoted - regressed,
        "frontier_ability_carrier_delta": {
            "additions": len(added_carriers),
            "removals": len(removed_carriers),
            "reclassifications": len(reclassified_carriers),
        },
        "card_program_structural_carrier_reconciliation": {
            "availability": "aggregate_only",
            "additions": None,
            "removals": None,
            "reclassifications": None,
            "net_ability_record_delta": program_ability_delta,
            "oracle_exact_ability_node_delta": oracle_ability_delta,
            "unresolved_structural_balance": (
                program_ability_delta - oracle_ability_delta
            ),
            "reason": (
                "Historical aggregate receipts retain the net CardProgram "
                "ability-record inventory but not individual structural "
                "carrier records. Addition, removal, and reclassification "
                "counts therefore remain explicitly unknown."
            ),
        },
        "interaction_assurance_delta": _count_delta(
            base["interaction_assurance"], head["interaction_assurance"]
        ),
        "architecture_delta": _count_delta(
            base["architecture"], head["architecture"]
        ),
    }


def _worktree_semantic_state(root: Path) -> dict[str, Any]:
    receipt = _worktree_receipt(root)
    return {
        "compiler_version": receipt["compiler_version"],
        "card_program_schema_version": receipt["card_program_schema_version"],
        "semantic_receipt_sha256": {
            path: receipt["blobs"][path]["semantic_sha256"]
            for path in (_PROGRAM_PATH, _ORACLE_PATH, _FRONTIER_PATH)
        },
        "support_counts": {
            "oracle_exact_cards": receipt["oracle_status_counts"].get("exact", 0),
            "trusted_card_programs": receipt["trusted_programs"],
            "capability_closed_card_programs": receipt[
                "capability_closed_programs"
            ],
            "oracle_material_residuals": receipt["oracle_material_residuals"],
            "card_program_material_residuals": receipt[
                "card_program_material_residuals"
            ],
            "card_program_ability_records": receipt[
                "card_program_ability_records"
            ],
            "hard_construction_failures": receipt[
                "hard_construction_failures"
            ],
        },
    }


def validated_semantic_transition_declaration(
    declaration: Any,
) -> dict[str, Any]:
    common_fields = {
        "transition_id",
        "compiler_version",
        "bundle_id",
        "candidate_ids",
        "family_ids",
        "capability_ids",
        "non_harvest_reason",
    }
    fields = set(declaration) if isinstance(declaration, Mapping) else set()
    legacy_prediction = fields == common_fields | {
        "expected_complete_card_gain"
    }
    generated_prediction = fields == common_fields | {"measurement_id"}
    if not isinstance(declaration, Mapping) or not (
        legacy_prediction or generated_prediction
    ):
        raise HarvestOutcomeHistoryError(
            "Changed semantic support requires one transition declaration"
        )
    transition_id = str(declaration.get("transition_id") or "")
    compiler_version = str(declaration.get("compiler_version") or "")
    bundle_id = declaration.get("bundle_id")
    raw_candidate_ids = declaration.get("candidate_ids")
    raw_family_ids = declaration.get("family_ids")
    raw_capability_ids = declaration.get("capability_ids")
    if not all(
        isinstance(value, list)
        for value in (
            raw_candidate_ids,
            raw_family_ids,
            raw_capability_ids,
        )
    ):
        raise HarvestOutcomeHistoryError(
            "Semantic transition identity fields must be arrays"
        )
    candidate_ids = [str(value) for value in raw_candidate_ids]
    family_ids = [str(value) for value in raw_family_ids]
    capability_ids = [
        str(value) for value in raw_capability_ids
    ]
    expected_gain = declaration.get("expected_complete_card_gain")
    measurement_id = declaration.get("measurement_id")
    non_harvest_reason = declaration.get("non_harvest_reason")
    harvest = (
        isinstance(bundle_id, str)
        and bundle_id.startswith("bundle:")
        and candidate_ids
        and all(candidate_ids)
        and candidate_ids == sorted(set(candidate_ids))
        and family_ids
        and all(family_ids)
        and family_ids == sorted(set(family_ids))
        and capability_ids
        and all(capability_ids)
        and capability_ids == sorted(set(capability_ids))
        and non_harvest_reason is None
        and (
            (
                legacy_prediction
                and (
                    expected_gain is None
                    or (type(expected_gain) is int and expected_gain >= 0)
                )
            )
            or (
                generated_prediction
                and isinstance(measurement_id, str)
                and measurement_id
                == "measurement:" + str(bundle_id).split(":", 1)[-1]
            )
        )
    )
    non_harvest = (
        legacy_prediction
        and
        bundle_id is None
        and not candidate_ids
        and not family_ids
        and not capability_ids
        and expected_gain is None
        and isinstance(non_harvest_reason, str)
        and len(non_harvest_reason.strip()) >= 20
    )
    if (
        not transition_id
        or not compiler_version.startswith("oracle-ir-v")
        or not (harvest or non_harvest)
    ):
        raise HarvestOutcomeHistoryError(
            "Semantic transition declarations must identify one harvest or "
            "give a precise non-harvest reason"
        )
    return {
        **dict(declaration),
        "candidate_ids": candidate_ids,
        "family_ids": family_ids,
        "capability_ids": capability_ids,
        "outcome_kind": "harvest" if harvest else "non_harvest",
    }


def _pending_transition(
    declaration: Any,
    *,
    current: Mapping[str, Any],
) -> dict[str, Any]:
    validated = validated_semantic_transition_declaration(declaration)
    return {
        **validated,
        "compiler_version": current["compiler_version"],
        "card_program_schema_version": current[
            "card_program_schema_version"
        ],
        "semantic_receipt_sha256": current["semantic_receipt_sha256"],
        "support_counts": current["support_counts"],
        "grants_gameplay_trust": False,
        "resolution": (
            "Generate the complete semantic receipts so the scheduler can "
            "materialize this content-bound outcome in the same feature fixed "
            "point before the selector admits another semantic harvest."
        ),
    }


def _semantic_outcome_state(
    latest_receipt: Mapping[str, Any],
    current: Mapping[str, Any],
    transition_declaration: Any,
) -> tuple[str, dict[str, Any] | None]:
    blobs = latest_receipt.get("blobs")
    current_hashes = current.get("semantic_receipt_sha256")
    if not isinstance(blobs, Mapping) or not isinstance(
        current_hashes, Mapping
    ):
        raise HarvestOutcomeHistoryError(
            "Semantic transition receipts are incomplete"
        )
    latest_hashes: dict[str, str] = {}
    for path in (_PROGRAM_PATH, _ORACLE_PATH, _FRONTIER_PATH):
        identity = blobs.get(path)
        if not isinstance(identity, Mapping) or not str(
            identity.get("raw_sha256") or ""
        ):
            raise HarvestOutcomeHistoryError(
                "Latest semantic outcome lacks a receipt identity"
            )
        latest_hashes[path] = str(
            identity.get("semantic_sha256") or identity["raw_sha256"]
        )
    semantic_current = latest_hashes == dict(current_hashes)
    if semantic_current:
        if transition_declaration is not None:
            raise HarvestOutcomeHistoryError(
                "Semantic transition declaration is stale after outcome materialization"
            )
        return "current", None
    return (
        "pending",
        _pending_transition(
            transition_declaration,
            current=current,
        ),
    )


_CONTENT_ENTRY_EXTRA_FIELDS = {
    "transition_id",
    "family_ids",
    "capability_ids",
    "receipt_identity_kind",
    "entry_fingerprint",
}
_MEASURED_CONTENT_ENTRY_FIELDS = {
    "measurement_id",
    "measurement_probe_id",
    "measurement_receipt_fingerprint",
    "measurement_frontier_fingerprint",
}
_FORECAST_CORRECTION_FIELDS = {
    "transition_id",
    "original_expected_complete_card_gain",
    "certified_complete_card_lower_bound",
    "certified_exact_ability_lower_bound",
    "certified_material_residual_reduction_lower_bound",
    "measurement_probe_id",
    "reason",
}


def _validated_forecast_correction(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _FORECAST_CORRECTION_FIELDS:
        raise HarvestOutcomeHistoryError(
            "Harvest forecast correction has an invalid shape"
        )
    correction = dict(value)
    transition_id = str(correction.get("transition_id") or "")
    measurement_probe_id = str(correction.get("measurement_probe_id") or "")
    reason = str(correction.get("reason") or "").strip()
    integer_fields = (
        "original_expected_complete_card_gain",
        "certified_complete_card_lower_bound",
        "certified_exact_ability_lower_bound",
        "certified_material_residual_reduction_lower_bound",
    )
    if (
        not transition_id
        or not measurement_probe_id
        or len(reason) < 40
        or any(
            type(correction.get(field)) is not int or correction[field] < 0
            for field in integer_fields
        )
        or correction["certified_complete_card_lower_bound"]
        > correction["original_expected_complete_card_gain"]
    ):
        raise HarvestOutcomeHistoryError(
            "Harvest forecast correction is incomplete or unbounded"
        )
    correction["transition_id"] = transition_id
    correction["measurement_probe_id"] = measurement_probe_id
    correction["reason"] = reason
    return correction


def _validated_forecast_corrections(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise HarvestOutcomeHistoryError(
            "Harvest forecast corrections must be an array"
        )
    corrections = [_validated_forecast_correction(row) for row in value]
    transition_ids = [row["transition_id"] for row in corrections]
    if transition_ids != sorted(set(transition_ids)):
        raise HarvestOutcomeHistoryError(
            "Harvest forecast corrections must have unique sorted transition IDs"
        )
    return corrections


def _apply_forecast_corrections(
    entries: list[dict[str, Any]], corrections: Any
) -> None:
    validated = _validated_forecast_corrections(corrections)
    by_transition = {row["transition_id"]: row for row in validated}
    content_entries = {
        str(row.get("transition_id") or ""): row
        for row in entries
        if row.get("receipt_identity_kind") == "semantic_content"
    }
    for transition_id, entry in content_entries.items():
        tracked = entry.get("forecast_correction")
        source = by_transition.get(transition_id)
        if tracked is not None and (
            source is None
            or _validated_forecast_correction(tracked) != source
        ):
            raise HarvestOutcomeHistoryError(
                "Tracked harvest forecast correction cannot disappear or mutate"
            )
    for correction in validated:
        transition_id = correction["transition_id"]
        entry = content_entries.get(transition_id)
        if entry is None:
            raise HarvestOutcomeHistoryError(
                "Harvest forecast correction must identify a content-bound outcome"
            )
        expected = entry.get("expected_complete_card_gain")
        actual_cards = entry.get("actual_complete_card_gain")
        actual_abilities = entry.get("actual_exact_ability_gain")
        actual_residuals = entry.get("actual_material_residual_reduction")
        if (
            expected
            != correction["original_expected_complete_card_gain"]
            or type(actual_cards) is not int
            or actual_cards < correction["certified_complete_card_lower_bound"]
            or type(actual_abilities) is not int
            or actual_abilities < correction["certified_exact_ability_lower_bound"]
            or type(actual_residuals) is not int
            or actual_residuals
            < correction[
                "certified_material_residual_reduction_lower_bound"
            ]
        ):
            raise HarvestOutcomeHistoryError(
                "Harvest forecast correction contradicts its realized outcome"
            )
        if (
            correction["certified_complete_card_lower_bound"] == expected
            and correction["measurement_probe_id"]
            == entry.get("measurement_probe_id")
        ):
            raise HarvestOutcomeHistoryError(
                "Secondary-metric harvest correction requires a new probe identity"
            )
        if entry.get("forecast_correction") is None:
            entry["forecast_correction"] = correction
            entry.pop("entry_fingerprint", None)
            entry["entry_fingerprint"] = _hash(entry)


def _receipt_content_fingerprint(receipt: Mapping[str, Any]) -> str:
    blobs = receipt.get("blobs")
    if not isinstance(blobs, Mapping):
        raise HarvestOutcomeHistoryError("Harvest content receipt lacks blobs")
    try:
        identities = {
            path: str(
                blobs[path].get("semantic_sha256")
                or blobs[path]["raw_sha256"]
            )
            for path, _compressed in _report_specs()
        }
    except (KeyError, TypeError) as exc:
        raise HarvestOutcomeHistoryError(
            "Harvest content receipt is incomplete"
        ) from exc
    if any(not value for value in identities.values()):
        raise HarvestOutcomeHistoryError(
            "Harvest content receipt identities are empty"
        )
    return _hash(
        {
            "schema_version": 1,
            "semantic_receipt_sha256": identities,
        }
    )


def _content_public_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    public = _public_receipt(receipt)
    public.pop("commit", None)
    public["content_fingerprint"] = _receipt_content_fingerprint(receipt)
    return public


def _transition_measurement_receipt(
    repository: Path,
    declaration: Mapping[str, Any],
) -> dict[str, Any] | None:
    measurement_id = declaration.get("measurement_id")
    if measurement_id is None:
        return None
    path = repository / _COHORT_MEASUREMENTS_PATH
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HarvestOutcomeHistoryError(
            "Generated transition cohort measurement is unavailable"
        ) from exc
    unsigned_artifact = dict(artifact)
    artifact_fingerprint = unsigned_artifact.pop("fingerprint", None)
    rows = artifact.get("transition_measurements")
    if artifact_fingerprint != _hash(unsigned_artifact) or not isinstance(
        rows, list
    ):
        raise HarvestOutcomeHistoryError(
            "Generated transition cohort artifact is malformed"
        )
    matches = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        measurement = raw.get("measurement")
        if (
            raw.get("transition_id") == declaration.get("transition_id")
            and isinstance(measurement, Mapping)
            and measurement.get("measurement_id") == measurement_id
        ):
            matches.append(dict(raw))
    if len(matches) != 1:
        raise HarvestOutcomeHistoryError(
            "Generated transition cohort measurement is not unique"
        )
    receipt = matches[0]
    unsigned_receipt = dict(receipt)
    receipt_fingerprint = unsigned_receipt.pop("receipt_fingerprint", None)
    measurement = receipt.get("measurement")
    metric_fields = (
        "affected_commander_cards",
        "complete_card_gain",
        "one_additional_blocker_cards",
        "two_additional_blocker_cards",
        "exact_ability_gain",
        "material_residual_reduction",
    )
    if (
        receipt_fingerprint != _hash(unsigned_receipt)
        or not str(receipt.get("frontier_fingerprint") or "")
        or not str(receipt.get("oracle_source_sha256") or "")
        or not isinstance(measurement, Mapping)
        or measurement.get("bundle_id") != declaration.get("bundle_id")
        or measurement.get("decision") != "bounded_executable"
        or measurement.get("grants_gameplay_trust") is not False
        or not str(measurement.get("probe_id") or "")
        or not str(measurement.get("cohort_fingerprint") or "")
        or any(
            type(measurement.get(field)) is not int
            or measurement[field] < 0
            for field in metric_fields
        )
    ):
        raise HarvestOutcomeHistoryError(
            "Generated transition cohort measurement is invalid"
        )
    return receipt


def _semantic_blob_sha256(
    path: str,
    identity: Mapping[str, Any],
    *,
    repository: Path | None,
) -> str:
    semantic = str(identity.get("semantic_sha256") or "")
    if semantic:
        return semantic
    raw_sha256 = str(identity.get("raw_sha256") or "")
    if repository is None:
        return raw_sha256
    oid = str(identity.get("git_blob_oid") or "")
    if not _COMMIT.fullmatch(oid):
        raise HarvestOutcomeHistoryError(
            "Historical frontier receipt lacks a canonical Git blob identity"
        )
    raw = _git(repository, "cat-file", "blob", oid)
    value = _json_object(
        raw,
        path,
        compressed=path in {_FRONTIER_PATH, _INTERACTIONS_PATH},
    )
    return _semantic_report_sha256(path, raw, value)


def _semantic_receipts_match(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    repository: Path | None = None,
) -> bool:
    try:
        return all(
            _semantic_blob_sha256(
                path,
                left["blobs"][path],
                repository=repository,
            )
            == _semantic_blob_sha256(
                path,
                right["blobs"][path],
                repository=repository,
            )
            for path in (_PROGRAM_PATH, _ORACLE_PATH, _FRONTIER_PATH)
        )
    except (KeyError, TypeError):
        return False


def _content_entry(
    declaration: Mapping[str, Any],
    *,
    base: Mapping[str, Any],
    head: Mapping[str, Any],
    measurement_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if (
        base["card_data_snapshot"] != head["card_data_snapshot"]
        or base["cards_considered"] != head["cards_considered"]
    ):
        raise HarvestOutcomeHistoryError(
            "Harvest corpus receipts must use one pinned card snapshot"
        )
    expected_gain = declaration.get("expected_complete_card_gain")
    measurement_fields: dict[str, Any] = {}
    if "measurement_id" in declaration:
        if measurement_receipt is None:
            raise HarvestOutcomeHistoryError(
                "Content-bound harvest lacks its generated cohort measurement"
            )
        measurement = measurement_receipt["measurement"]
        expected_gain = measurement["complete_card_gain"]
        measurement_fields = {
            "measurement_id": declaration["measurement_id"],
            "measurement_probe_id": measurement["probe_id"],
            "measurement_receipt_fingerprint": measurement_receipt[
                "receipt_fingerprint"
            ],
            "measurement_frontier_fingerprint": measurement_receipt[
                "frontier_fingerprint"
            ],
        }
    entry = {
        "transition_id": str(declaration["transition_id"]),
        "bundle_id": str(declaration["bundle_id"]),
        "candidate_ids": list(declaration["candidate_ids"]),
        "family_ids": list(declaration["family_ids"]),
        "capability_ids": list(declaration["capability_ids"]),
        "expected_complete_card_gain": expected_gain,
        "expected_complete_card_gain_basis": (
            "generated_transition_cohort"
            if measurement_fields
            else "authoritative_source"
            if expected_gain is not None
            else "not_captured"
        ),
        "receipt_identity_kind": "semantic_content",
        "base_receipt": _content_public_receipt(base),
        "head_receipt": _content_public_receipt(head),
        **measurement_fields,
        **_transition_metrics(base, head),
    }
    entry["entry_fingerprint"] = _hash(entry)
    return entry


def _validate_content_entry(
    entry: Any,
) -> dict[str, Any]:
    if not isinstance(entry, Mapping):
        raise HarvestOutcomeHistoryError(
            "Content-bound harvest outcome must be an object"
        )
    candidate = dict(entry)
    fingerprint = candidate.pop("entry_fingerprint", None)
    base = candidate.get("base_receipt")
    head = candidate.get("head_receipt")
    measured_fields = set(candidate).intersection(
        _MEASURED_CONTENT_ENTRY_FIELDS
    )
    if (
        set(entry) != set(candidate) | {"entry_fingerprint"}
        or fingerprint != _hash(candidate)
        or candidate.get("receipt_identity_kind") != "semantic_content"
        or not str(candidate.get("transition_id") or "")
        or not isinstance(base, Mapping)
        or not isinstance(head, Mapping)
        or "commit" in base
        or "commit" in head
        or base.get("content_fingerprint")
        != _receipt_content_fingerprint(base)
        or head.get("content_fingerprint")
        != _receipt_content_fingerprint(head)
        or measured_fields not in (set(), _MEASURED_CONTENT_ENTRY_FIELDS)
        or (
            measured_fields
            and (
                candidate.get("expected_complete_card_gain_basis")
                != "generated_transition_cohort"
                or not str(candidate.get("measurement_id") or "")
                or not str(candidate.get("measurement_probe_id") or "")
                or not str(
                    candidate.get("measurement_receipt_fingerprint") or ""
                )
                or not str(
                    candidate.get("measurement_frontier_fingerprint") or ""
                )
            )
        )
    ):
        raise HarvestOutcomeHistoryError(
            "Content-bound harvest outcome is malformed"
        )
    correction = candidate.get("forecast_correction")
    if correction is not None:
        validated_correction = _validated_forecast_correction(correction)
        if validated_correction["transition_id"] != candidate["transition_id"]:
            raise HarvestOutcomeHistoryError(
                "Harvest forecast correction transition is inconsistent"
            )
    return dict(entry)


def _tracked_content_entries(
    repository: Path,
    *,
    legacy_entries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    path = repository / "coverage" / "harvest-outcome-history.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if value.get("schema_version") != HARVEST_HISTORY_SCHEMA_VERSION:
        return []
    rows = value.get("entries")
    if not isinstance(rows, list) or len(rows) < len(legacy_entries):
        raise HarvestOutcomeHistoryError(
            "Tracked content-bound harvest history is truncated"
        )
    content_rows = rows[len(legacy_entries) :]
    result: list[dict[str, Any]] = []
    for row in content_rows:
        validated = _validate_content_entry(row)
        result.append(validated)
    return result


def _declaration_matches_content_entry(
    declaration: Mapping[str, Any], entry: Mapping[str, Any]
) -> bool:
    return (
        declaration.get("compiler_version")
        == entry.get("head_receipt", {}).get("compiler_version")
        and all(
        declaration.get(field) == entry.get(field)
        for field in (
            "transition_id",
            "bundle_id",
            "candidate_ids",
            "family_ids",
            "capability_ids",
        )
        )
        and (
            declaration.get("measurement_id")
            == entry.get("measurement_id")
            if "measurement_id" in declaration
            else declaration.get("expected_complete_card_gain")
            == entry.get("expected_complete_card_gain")
        )
    )


def _refresh_content_entry(
    entry: Mapping[str, Any],
    *,
    declaration: Mapping[str, Any],
    head: Mapping[str, Any],
    repository: Path | None = None,
    measurement_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    validated = _validate_content_entry(entry)
    if not _declaration_matches_content_entry(declaration, validated):
        raise HarvestOutcomeHistoryError(
            "Only the matching content-bound transition can be refreshed"
        )
    if not _semantic_receipts_match(
        validated["head_receipt"],
        head,
        repository=repository,
    ):
        raise HarvestOutcomeHistoryError(
            "A semantic content change requires a new harvest outcome"
        )
    base = validated["base_receipt"]
    refreshed = {
        **validated,
        "head_receipt": _content_public_receipt(head),
        "interaction_assurance_delta": _count_delta(
            base["interaction_assurance"], head["interaction_assurance"]
        ),
        "architecture_delta": _count_delta(
            base["architecture"], head["architecture"]
        ),
    }
    if "measurement_id" in declaration and measurement_receipt is not None:
        measurement = measurement_receipt["measurement"]
        refreshed.update(
            {
                "expected_complete_card_gain": measurement[
                    "complete_card_gain"
                ],
                "measurement_id": declaration["measurement_id"],
                "measurement_probe_id": measurement["probe_id"],
                "measurement_receipt_fingerprint": measurement_receipt[
                    "receipt_fingerprint"
                ],
                "measurement_frontier_fingerprint": measurement_receipt[
                    "frontier_fingerprint"
                ],
            }
        )
    refreshed.pop("entry_fingerprint", None)
    refreshed["entry_fingerprint"] = _hash(refreshed)
    return refreshed


def _content_transition_is_landed(
    repository: Path, transition_id: str
) -> bool:
    durable_tip = _durable_main_tip(repository)
    completed = subprocess.run(
        [
            "git",
            "show",
            f"{durable_tip}:coverage/harvest-outcome-history.json",
        ],
        cwd=repository,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        return False
    try:
        value = json.loads(completed.stdout)
    except (UnicodeError, json.JSONDecodeError):
        return False
    entries = value.get("entries") if isinstance(value, Mapping) else None
    return isinstance(entries, list) and any(
        isinstance(row, Mapping)
        and row.get("receipt_identity_kind") == "semantic_content"
        and row.get("transition_id") == transition_id
        for row in entries
    )


def _tracked_legacy_entries(
    repository: Path,
    provenance_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    path = repository / "coverage" / "harvest-outcome-history.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if value.get("schema_version") != HARVEST_HISTORY_SCHEMA_VERSION:
        return []
    unsigned = dict(value)
    fingerprint = unsigned.pop("fingerprint", None)
    if fingerprint != _hash(unsigned):
        raise HarvestOutcomeHistoryError(
            "Tracked harvest outcome history fingerprint is stale"
        )
    entries = value.get("entries")
    if not isinstance(entries, list) or len(entries) < len(provenance_rows):
        raise HarvestOutcomeHistoryError(
            "Tracked legacy harvest outcome history is truncated"
        )
    result: list[dict[str, Any]] = []
    for source, entry in zip(
        provenance_rows,
        entries[: len(provenance_rows)],
        strict=True,
    ):
        if (
            not isinstance(entry, Mapping)
            or entry.get("receipt_identity_kind") is not None
            or entry.get("bundle_id") != source.get("bundle_id")
            or entry.get("candidate_ids") != source.get("candidate_ids")
            or entry.get("expected_complete_card_gain")
            != source.get("expected_complete_card_gain")
        ):
            raise HarvestOutcomeHistoryError(
                "Tracked legacy harvest outcome contradicts its provenance"
            )
        result.append(dict(entry))
    return result


def build_harvest_outcome_history(
    root: str | Path,
    provenance: Any,
    transition_declaration: Any = None,
    forecast_corrections: Any = None,
) -> dict[str, Any]:
    repository = Path(root).resolve()
    expected = {
        "bundle_id",
        "candidate_ids",
        "expected_complete_card_gain",
        "base_commit",
        "head_commit",
    }
    seen_bundles: set[str] = set()
    receipt_cache: dict[str, dict[str, Any]] = {}

    def receipt(commit: str) -> dict[str, Any]:
        value = receipt_cache.get(commit)
        if value is None:
            value = _receipt(repository, commit)
            receipt_cache[commit] = value
        return value

    provenance_rows = _provenance_rows(provenance)
    cached_legacy_entries = _tracked_legacy_entries(
        repository, provenance_rows
    )
    entries: list[dict[str, Any]] = []
    for index, row in enumerate(provenance_rows):
        if set(row) != expected:
            raise HarvestOutcomeHistoryError(
                f"Harvest provenance row {index} has an invalid shape"
            )
        bundle_id = str(row.get("bundle_id") or "")
        candidate_ids = [str(value) for value in row.get("candidate_ids", [])]
        expected_gain = row.get("expected_complete_card_gain")
        if (
            not bundle_id.startswith("bundle:")
            or bundle_id in seen_bundles
            or not candidate_ids
            or candidate_ids != sorted(set(candidate_ids))
            or (
                expected_gain is not None
                and (type(expected_gain) is not int or expected_gain < 0)
            )
        ):
            raise HarvestOutcomeHistoryError(
                "Harvest provenance identity and prediction fields are invalid"
            )
        seen_bundles.add(bundle_id)
        base_commit = _canonical_commit(
            repository, row.get("base_commit"), "base_commit"
        )
        head_commit = _canonical_commit(
            repository, row.get("head_commit"), "head_commit"
        )
        _require_landed_harvest_head(repository, head_commit)
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", base_commit, head_commit],
            cwd=repository,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if ancestor.returncode != 0 or base_commit == head_commit:
            raise HarvestOutcomeHistoryError(
                "Harvest base must be a strict ancestor of its head"
            )
        if cached_legacy_entries:
            entries.append(cached_legacy_entries[index])
            continue
        base = receipt(base_commit)
        head = receipt(head_commit)
        if (
            base["card_data_snapshot"] != head["card_data_snapshot"]
            or base["cards_considered"] != head["cards_considered"]
        ):
            raise HarvestOutcomeHistoryError(
                "Harvest corpus receipts must use one pinned card snapshot"
            )
        entries.append(
            {
                "bundle_id": bundle_id,
                "candidate_ids": candidate_ids,
                "expected_complete_card_gain": expected_gain,
                "expected_complete_card_gain_basis": (
                    "authoritative_source"
                    if expected_gain is not None
                    else "not_captured"
                ),
                "base_receipt": _public_receipt(base),
                "head_receipt": _public_receipt(head),
                **_transition_metrics(base, head),
            }
        )
    if not entries:
        raise HarvestOutcomeHistoryError(
            "Harvest history requires at least one immutable outcome"
        )
    entries.extend(
        _tracked_content_entries(
            repository,
            legacy_entries=entries,
        )
    )
    if len({str(row["bundle_id"]) for row in entries}) != len(entries):
        raise HarvestOutcomeHistoryError(
            "Harvest outcome bundle identities must remain unique"
        )
    current_receipt = _worktree_receipt(repository)
    latest = entries[-1]["head_receipt"]
    semantic_outcome_status = "current"
    pending = None
    validated_declaration = (
        validated_semantic_transition_declaration(transition_declaration)
        if transition_declaration is not None
        else None
    )
    if _semantic_receipts_match(
        latest,
        current_receipt,
        repository=repository,
    ):
        if validated_declaration is not None:
            matching_content_entry = (
                entries[-1].get("receipt_identity_kind") == "semantic_content"
                and _declaration_matches_content_entry(
                    validated_declaration, entries[-1]
                )
            )
            if (
                matching_content_entry
                and _receipt_content_fingerprint(latest)
                != _receipt_content_fingerprint(current_receipt)
                and not _content_transition_is_landed(
                    repository,
                    validated_declaration["transition_id"],
                )
            ):
                entries[-1] = _refresh_content_entry(
                    entries[-1],
                    declaration=validated_declaration,
                    head=current_receipt,
                    repository=repository,
                    measurement_receipt=_transition_measurement_receipt(
                        repository,
                        validated_declaration,
                    ),
                )
                latest = entries[-1]["head_receipt"]
            elif (
                not matching_content_entry
                and validated_declaration["compiler_version"]
                == current_receipt["compiler_version"]
            ):
                raise HarvestOutcomeHistoryError(
                    "Semantic transition declaration is stale before a content change"
                )
    elif (
        validated_declaration is not None
        and validated_declaration["outcome_kind"] == "harvest"
    ):
        if validated_declaration["compiler_version"] != current_receipt[
            "compiler_version"
        ]:
            raise HarvestOutcomeHistoryError(
                "Semantic transition compiler version does not match its receipt"
            )
        base = receipt(_durable_main_tip(repository))
        if any(
            row.get("transition_id") == validated_declaration["transition_id"]
            for row in entries
        ):
            raise HarvestOutcomeHistoryError(
                "Semantic transition identity has already been materialized"
            )
        entries.append(
            _content_entry(
                validated_declaration,
                base=base,
                head=current_receipt,
                measurement_receipt=_transition_measurement_receipt(
                    repository,
                    validated_declaration,
                ),
            )
        )
        latest = entries[-1]["head_receipt"]
    else:
        current = _worktree_semantic_state(repository)
        semantic_outcome_status, pending = _semantic_outcome_state(
            latest,
            current,
            transition_declaration,
        )

    _apply_forecast_corrections(entries, forecast_corrections)

    payload: dict[str, Any] = {
        "schema_version": HARVEST_HISTORY_SCHEMA_VERSION,
        "algorithm_version": HARVEST_HISTORY_ALGORITHM_VERSION,
        "legacy_provenance_fingerprint": _hash(provenance_rows),
        "entries": entries,
        "outcome_basis": (
            "Actual outcomes are derived from immutable semantic content "
            "receipts for the "
            "pinned Commander Oracle and CardProgram coverage, complete card "
            "frontier, reusable-piece interaction matrix, and architecture audit."
        ),
        "structural_carrier_limitation": (
            "Historical coverage receipts retain net CardProgram ability-record "
            "counts but not individual structural carrier inventories. Exact "
            "addition, removal, and reclassification counts remain null and are "
            "reconciled through the aggregate balance."
        ),
        "semantic_outcome_status": semantic_outcome_status,
        "pending_transition": pending,
    }
    payload["fingerprint"] = _hash(payload)
    return payload


__all__ = [
    "build_harvest_outcome_history",
    "HARVEST_HISTORY_ALGORITHM_VERSION",
    "HARVEST_HISTORY_SCHEMA_VERSION",
    "HarvestOutcomeHistoryError",
]
