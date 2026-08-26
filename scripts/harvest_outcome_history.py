from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping, Sequence

from quorune.util import stable_json


HARVEST_HISTORY_SCHEMA_VERSION = 2
HARVEST_HISTORY_ALGORITHM_VERSION = "git-corpus-receipt-delta-v2"
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_PROGRAM_PATH = "coverage/card-program-coverage-commander.json"
_ORACLE_PATH = "coverage/oracle-coverage-commander.json"
_FRONTIER_PATH = "coverage/card-unlock-frontier.json.gz"
_INTERACTIONS_PATH = "coverage/reusable-piece-interactions.json.gz"
_ARCHITECTURE_PATH = "coverage/architecture-audit.json"


class HarvestOutcomeHistoryError(ValueError):
    pass


def _hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


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


def _receipt(root: Path, commit: str) -> dict[str, Any]:
    reports: dict[str, tuple[str, bytes, dict[str, Any]]] = {}
    report_specs = (
        (_PROGRAM_PATH, False),
        (_ORACLE_PATH, False),
        (_FRONTIER_PATH, True),
        (_INTERACTIONS_PATH, True),
        (_ARCHITECTURE_PATH, False),
    )
    blobs = _blobs(root, commit, tuple(path for path, _compressed in report_specs))
    for path, compressed in report_specs:
        oid, raw = blobs[path]
        reports[path] = (
            oid,
            raw,
            _json_object(raw, path, compressed=compressed),
        )
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
            }
            for path, (oid, raw, _value) in sorted(reports.items())
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
    reports: dict[str, tuple[bytes, dict[str, Any]]] = {}
    for path, compressed in (
        (_PROGRAM_PATH, False),
        (_ORACLE_PATH, False),
        (_FRONTIER_PATH, True),
    ):
        try:
            raw = (root / path).read_bytes()
        except OSError as exc:
            raise HarvestOutcomeHistoryError(
                f"Current semantic receipt is unavailable: {path}"
            ) from exc
        reports[path] = (
            raw,
            _json_object(raw, path, compressed=compressed),
        )
    program = reports[_PROGRAM_PATH][1]
    oracle = reports[_ORACLE_PATH][1]
    frontier = reports[_FRONTIER_PATH][1]
    program_statuses = _status_counts(
        program.get("status_counts"), "program.status_counts"
    )
    trust_basis = _status_counts(
        program.get("trust_basis_counts"), "program.trust_basis_counts"
    )
    oracle_statuses = _status_counts(
        oracle.get("status_counts"), "oracle.status_counts"
    )
    hard_failures = frontier.get("hard_construction_failures")
    if not isinstance(hard_failures, list):
        raise HarvestOutcomeHistoryError(
            "Frontier hard construction failures must be an array"
        )
    return {
        "compiler_version": str(program.get("compiler_version") or ""),
        "card_program_schema_version": _nonnegative_int(
            program.get("card_program_schema_version"),
            "program.card_program_schema_version",
        ),
        "semantic_receipt_sha256": {
            path: hashlib.sha256(raw).hexdigest()
            for path, (raw, _value) in sorted(reports.items())
        },
        "support_counts": {
            "oracle_exact_cards": oracle_statuses.get("exact", 0),
            "trusted_card_programs": program_statuses.get("trusted", 0),
            "capability_closed_card_programs": trust_basis.get(
                "capability_closed", 0
            ),
            "oracle_material_residuals": _nonnegative_int(
                oracle.get("material_residuals"), "oracle.material_residuals"
            ),
            "card_program_material_residuals": _nonnegative_int(
                program.get("material_residuals"),
                "program.material_residuals",
            ),
            "card_program_ability_records": _nonnegative_int(
                program.get("ability_programs"), "program.ability_programs"
            ),
            "hard_construction_failures": len(hard_failures),
        },
    }


def validated_semantic_transition_declaration(
    declaration: Any,
) -> dict[str, Any]:
    if not isinstance(declaration, Mapping) or set(declaration) != {
        "transition_id",
        "bundle_id",
        "candidate_ids",
        "family_ids",
        "capability_ids",
        "expected_complete_card_gain",
        "non_harvest_reason",
    }:
        raise HarvestOutcomeHistoryError(
            "Changed semantic support requires one transition declaration"
        )
    transition_id = str(declaration.get("transition_id") or "")
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
    )
    non_harvest = (
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
        or not (harvest or non_harvest)
        or (
            expected_gain is not None
            and (type(expected_gain) is not int or expected_gain < 0)
        )
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
            "Commit the generated corpus receipts, then materialize this "
            "downstream outcome from that immutable Git commit before the "
            "selector admits another semantic harvest."
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
        latest_hashes[path] = str(identity["raw_sha256"])
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


def build_harvest_outcome_history(
    root: str | Path,
    provenance: Any,
    transition_declaration: Any = None,
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

    entries: list[dict[str, Any]] = []
    for index, row in enumerate(_provenance_rows(provenance)):
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
    current = _worktree_semantic_state(repository)
    latest = entries[-1]["head_receipt"]
    semantic_outcome_status, pending = _semantic_outcome_state(
        latest,
        current,
        transition_declaration,
    )

    payload: dict[str, Any] = {
        "schema_version": HARVEST_HISTORY_SCHEMA_VERSION,
        "algorithm_version": HARVEST_HISTORY_ALGORITHM_VERSION,
        "entries": entries,
        "outcome_basis": (
            "Actual outcomes are derived from immutable Git blobs for the "
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
