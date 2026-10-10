from __future__ import annotations

"""Rebind the new delivery cohort selected by a verified current census."""

import gzip
import json
from pathlib import Path
from typing import Any, Mapping

from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry


ROOT = Path(__file__).resolve().parents[1]
FRONTIER = ROOT / "coverage/card-unlock-frontier.json.gz"
PROBE_ID = "batched-card-support-current-census-v2"


def current_census_delivery_measurement(
    *, frontier: Mapping[str, Any], bundle_id: str, probe_id: str,
    cards_by_oracle_id: Mapping[str, Any], coverage: Mapping[str, Any],
    cohort_fingerprint: str, database: Any,
) -> dict[str, Any]:
    if probe_id != PROBE_ID or database is None:
        raise ValueError("Delivery measurement requires its versioned probe and pinned database")
    current = json.loads(gzip.decompress(FRONTIER.read_bytes()))
    registry = load_default_capability_registry()
    if (current.get("capability_registry_fingerprint") != registry.fingerprint
            or current.get("capability_evidence_fingerprint") != registry.evidence_fingerprint
            or current.get("limited") or not current.get("commander_legal_only")):
        raise ValueError("Delivery measurement requires the current complete capability-bound Commander frontier")
    if current.get("card_data_snapshot") != frontier.get("card_data_snapshot"):
        raise ValueError("Delivery measurement baseline and current snapshot differ")
    baseline = {str(row["oracle_id"]): row for row in frontier["cards"]}
    candidates = sorted({str(row["oracle_id"]) for row in current["cards"]
                         if row.get("card_program_trust_basis") == "capability_closed"
                         and str(row["oracle_id"]) in baseline
                         and baseline[str(row["oracle_id"])].get("card_program_trust_basis") != "capability_closed"})
    semantics = SemanticRegistry()
    complete = set()
    abilities = residuals = 0
    for oracle_id in candidates:
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise ValueError("Delivery measurement is missing a pinned Commander record")
        program = compile_best_available_card_program(database, record, semantic_registry=semantics,
            capability_registry=registry, capability_profile="commander_review")
        if not bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")["strict_capability_ready"]:
            continue
        complete.add(oracle_id)
        ir = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        old = baseline[oracle_id].get("abilities", ())
        abilities += max(0, sum(node.exact for face in ir.faces for node in face.nodes) - sum(row.get("status") == "exact" for row in old))
        residuals += max(0, sum(len(row.get("residuals", ())) for row in old) - len(ir.material_residuals))
    return {
        "measurement_id": "measurement:" + bundle_id.split(":", 1)[-1],
        "bundle_id": bundle_id, "probe_id": probe_id, "cohort_fingerprint": cohort_fingerprint,
        "affected_commander_cards": len(candidates), "complete_card_gain": len(complete),
        "one_additional_blocker_cards": 0, "two_additional_blocker_cards": 0,
        "exact_ability_gain": abilities, "material_residual_reduction": residuals,
        "decision": "bounded_executable" if len(complete) >= int(coverage["minimum_complete_card_gain"]) else "retired_below_harvest_floor",
        "grants_gameplay_trust": False,
    }
