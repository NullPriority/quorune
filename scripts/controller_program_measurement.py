from __future__ import annotations

"""Current-frontier measurements for the shared controller-program boundary."""

from typing import Any, Mapping

from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry


PROBE_ID = "controller-program-composition-existing-owner-v1"


def controller_program_measurement(
    *, frontier: Mapping[str, Any], bundle_id: str, probe_id: str,
    cards_by_oracle_id: Mapping[str, Any], coverage: Mapping[str, Any],
    cohort_fingerprint: str, database: Any,
) -> dict[str, Any]:
    """Recompile original source; never infer closure from independent leaves."""

    if database is None:
        raise ValueError("Controller-program closure requires the pinned database")
    registry = load_default_capability_registry()
    remaining = {}
    complete = set()
    ability_gain = residual_reduction = 0
    semantics: SemanticRegistry | None = None
    for card in frontier.get("cards", ()):
        if card.get("oracle_ir_status") == "exact":
            continue
        oracle_id = str(card["oracle_id"])
        record = cards_by_oracle_id[oracle_id]
        previous = {(str(ability.get("face_id") or "front"), str(ability["ability_id"])): ability
                    for ability in card.get("abilities", ())}
        if "If " not in record.oracle_text and not any(
            ability.get("status") == "lowerable_untrusted" for ability in previous.values()
        ):
            continue
        compiled = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        promoted = {(face.face_id, node.node_id) for face in compiled.faces for node in face.nodes
                    if node.exact and previous.get((face.face_id, node.node_id), {}).get("status") != "exact"}
        if not promoted:
            continue
        ability_gain += len(promoted)
        residual_reduction += sum(len(ability.get("residuals", ())) for identity, ability in previous.items() if identity in promoted)
        remaining[oracle_id] = sum(ability.get("status") != "exact" and identity not in promoted for identity, ability in previous.items())
        if compiled.status != "exact":
            continue
        if semantics is None:
            semantics = SemanticRegistry()
        program = compile_best_available_card_program(database, record, semantic_registry=semantics,
            capability_registry=registry, capability_profile="commander_review")
        if bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")["strict_capability_ready"]:
            complete.add(oracle_id)
    reaches_floor = bool(complete) and (
        len(complete) >= int(coverage["minimum_complete_card_gain"])
        or ability_gain >= int(coverage["minimum_exact_ability_gain"])
        or residual_reduction >= int(coverage["minimum_material_residual_reduction"])
    )
    return {
        "measurement_id": "measurement:" + bundle_id.split(":", 1)[-1],
        "bundle_id": bundle_id, "probe_id": probe_id, "cohort_fingerprint": cohort_fingerprint,
        "affected_commander_cards": len(remaining), "complete_card_gain": len(complete),
        "one_additional_blocker_cards": sum(count == 1 for count in remaining.values()),
        "two_additional_blocker_cards": sum(count == 2 for count in remaining.values()),
        "exact_ability_gain": ability_gain, "material_residual_reduction": residual_reduction,
        "decision": "bounded_executable" if reaches_floor else "retired_below_harvest_floor",
        "grants_gameplay_trust": False,
    }
