"""Original-source, whole-runtime measurement of the counted search contract."""

from __future__ import annotations

from typing import Any, Mapping

from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.library_search_model import FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry


PROBE_ID = "fixed-counted-library-search-existing-owner-v1"


def counted_library_search_measurement(
    *, frontier: Mapping[str, Any], bundle_id: str, probe_id: str,
    cards_by_oracle_id: Mapping[str, Any], coverage: Mapping[str, Any],
    cohort_fingerprint: str, database: Any,
    diagnostic_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if database is None:
        raise ValueError("Counted search measurement requires the pinned database")
    registry = load_default_capability_registry()
    semantics = SemanticRegistry()
    remaining: dict[str, int] = {}
    complete: set[str] = set()
    ability_gain = residual_reduction = 0
    for card in frontier.get("cards", ()):
        if card.get("oracle_ir_status") == "exact":
            continue
        oracle_id = str(card["oracle_id"])
        record = cards_by_oracle_id[oracle_id]
        texts = (
            tuple(str(face.get("oracle_text") or "") for face in record.faces)
            if record.faces else (record.oracle_text,)
        )
        if not any("search your library" in text.casefold() for text in texts):
            continue
        previous = {
            (str(node["face_id"]), str(node["ability_id"])): node
            for node in card["abilities"]
        }
        ir = compile_oracle_card(
            record, capability_registry=registry, capability_profile="commander_review",
        )
        promoted = {
            (face.face_id, node.node_id) for face in ir.faces for node in face.nodes
            if node.exact and FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID in node.capability_dependencies
            and (face.face_id, node.node_id) in previous
            and previous[(face.face_id, node.node_id)]["status"] != "exact"
        }
        if not promoted:
            continue
        ability_gain += len(promoted)
        reductions = sum(len(previous[key].get("residuals", ())) for key in promoted)
        residual_reduction += reductions
        remaining[oracle_id] = sum(
            node["status"] != "exact" and key not in promoted
            for key, node in previous.items()
        )
        closed = False
        if ir.status == "exact":
            program = compile_best_available_card_program(
                database, record, semantic_registry=semantics,
                capability_registry=registry, capability_profile="commander_review",
            )
            binding = bind_card_program_runtime(
                program, capability_registry=registry, profile="commander_review",
            )
            closed = binding["strict_capability_ready"]
            if closed:
                complete.add(oracle_id)
        if diagnostic_rows is not None:
            diagnostic_rows.append({
                "oracle_id": oracle_id, "name": record.name,
                "promoted_abilities": sorted(key[1] for key in promoted),
                "residual_reduction": reductions, "strict_whole_runtime_ready": closed,
            })
    qualifies = bool(complete) and (
        len(complete) >= int(coverage["minimum_complete_card_gain"])
        or ability_gain >= int(coverage["minimum_exact_ability_gain"])
        or residual_reduction >= int(coverage["minimum_material_residual_reduction"])
    )
    return {
        "measurement_id": "measurement:" + bundle_id.split(":", 1)[-1],
        "bundle_id": bundle_id, "probe_id": probe_id,
        "cohort_fingerprint": cohort_fingerprint,
        "affected_commander_cards": len(remaining), "complete_card_gain": len(complete),
        "one_additional_blocker_cards": sum(value == 1 for value in remaining.values()),
        "two_additional_blocker_cards": sum(value == 2 for value in remaining.values()),
        "exact_ability_gain": ability_gain, "material_residual_reduction": residual_reduction,
        "decision": "bounded_executable" if qualifies else "retired_below_harvest_floor",
        "grants_gameplay_trust": False,
    }
