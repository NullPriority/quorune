from __future__ import annotations

"""Measure original whole CardPrograms for the shared typed grant carriers."""

from typing import Any, Mapping

from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry


PROBE_ID = "typed-layer-six-grant-carriers-existing-owner-v1"
_TEMPLATES = frozenset({
    "continuous-public-conditional-typed-grant-v1",
    "continuous-attached-fixed-characteristics-granted-ability-v1",
    "continuous-fixed-query-granted-ability-v1",
})


def typed_grant_carrier_measurement(
    *, frontier: Mapping[str, Any], bundle_id: str, probe_id: str,
    cards_by_oracle_id: Mapping[str, Any], coverage: Mapping[str, Any],
    cohort_fingerprint: str, database: Any,
    diagnostic_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if database is None:
        raise ValueError("Typed grant measurement requires the pinned database")
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
        texts = tuple(str(f.get("oracle_text") or "") for f in record.faces) if record.faces else (record.oracle_text,)
        if not any('"' in text for text in texts):
            continue
        previous = {(str(n["face_id"]), str(n["ability_id"])): n for n in card["abilities"]}
        ir = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        promoted = [(face, n) for face in ir.faces for n in face.nodes
            if n.exact and n.template_id in _TEMPLATES
            and (face.face_id, n.node_id) in previous
            and previous[(face.face_id, n.node_id)]["status"] != "exact"]
        if not promoted:
            continue
        keys = {(f.face_id, n.node_id) for f,n in promoted}
        exact_delta = len(promoted) + sum(
            child.exact and child.node_id == n.node_id + ":granted"
            for face,n in promoted for child in face.nodes
        )
        ability_gain += exact_delta
        reduction = sum(len(previous[key].get("residuals", ())) for key in keys)
        residual_reduction += reduction
        remaining[oracle_id] = sum(n["status"] != "exact" and key not in keys for key,n in previous.items())
        closed = False
        if ir.status == "exact":
            program = compile_best_available_card_program(database, record, semantic_registry=semantics,
                capability_registry=registry, capability_profile="commander_review")
            closed = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")["strict_capability_ready"]
            if closed:
                complete.add(oracle_id)
        if diagnostic_rows is not None:
            diagnostic_rows.append({"name": record.name, "oracle_id": oracle_id,
                "strict_whole_runtime_ready": closed, "exact_ability_gain": exact_delta,
                "material_residual_reduction": reduction,
                "promoted_abilities": sorted(n.node_id for _,n in promoted)})
    qualifies = bool(complete) and (
        len(complete) >= int(coverage["minimum_complete_card_gain"])
        or ability_gain >= int(coverage["minimum_exact_ability_gain"])
        or residual_reduction >= int(coverage["minimum_material_residual_reduction"])
    )
    return {"measurement_id": "measurement:" + bundle_id.split(":", 1)[-1],
        "bundle_id": bundle_id, "probe_id": probe_id, "cohort_fingerprint": cohort_fingerprint,
        "affected_commander_cards": len(remaining), "complete_card_gain": len(complete),
        "one_additional_blocker_cards": sum(n == 1 for n in remaining.values()),
        "two_additional_blocker_cards": sum(n == 2 for n in remaining.values()),
        "exact_ability_gain": ability_gain, "material_residual_reduction": residual_reduction,
        "decision": "bounded_executable" if qualifies else "retired_below_harvest_floor",
        "grants_gameplay_trust": False}
