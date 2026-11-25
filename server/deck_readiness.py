from __future__ import annotations

from typing import Any

from quorune.card_programs.adapters import CardProgramSourceMismatch
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition
from quorune.preflight import semantic_preflight


_REASON_LABELS = {
    "cast_cost": "Casting cost needs additional rules support",
    "activated_ability": "Activated ability needs additional rules support",
    "triggered_ability": "Triggered ability needs additional rules support",
    "replacement_effect": "Replacement effect needs additional rules support",
    "semantic_source_hash_drift": "The verified rules use different card data",
}


def assess_deck(card_db: CardDatabase, deck: DeckDefinition) -> dict[str, Any]:
    """Keep a format-checked list available for review when card data conflicts."""
    try:
        report = semantic_preflight(card_db, deck)
    except CardProgramSourceMismatch as exc:
        try:
            card = card_db.by_oracle_id(exc.oracle_id).name
        except KeyError:
            card = exc.card_name or "Card requiring a data review"
        report = {"trusted_only_ready": False, "data_integrity_blockers": [exc.diagnostic(card)]}
    report["format_legality"] = deck.metadata.get("format_legality", {
        "schema_version": 1, "profile": "commander", "status": "legal", "issues": [],
    })
    return report


def has_integrity_blocker(report: dict[str, Any]) -> bool:
    return bool(report.get("data_integrity_blockers") or report.get("source_hash_drift_cards"))


def _affected_card(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("source_hash_match") is False:
        reasons = [_REASON_LABELS["semantic_source_hash_drift"]]
    elif row.get("status") in {"unresolved", "partial"}:
        reasons = list(dict.fromkeys(
            _REASON_LABELS.get(reason.split(":", 1)[0], "Card behavior needs review")
            for reason in row.get("unresolved", [])
        )) or ["Card behavior needs review"]
    elif (binding := row.get("card_program_runtime_binding")) and binding.get("blockers"):
        reasons = ["Card behavior needs verification for this release"]
    elif "card_program_fingerprint" in row and row["card_program_fingerprint"] is None:
        reasons = ["This format still needs a verified rules entry for this card"]
    else:
        return None
    return {"name": row["name"], "reasons": reasons}


def compact_deck_preflight(report: dict[str, Any]) -> dict[str, Any]:
    """Present a submitted deck's checks to the guest who owns it."""
    reasons_by_name: dict[str, list[str]] = {}
    for row in report.get("cards", []):
        if result := _affected_card(row):
            reasons = reasons_by_name.setdefault(result["name"], [])
            reasons.extend(reason for reason in result["reasons"] if reason not in reasons)
    affected = [{"name": name, "reasons": reasons} for name, reasons in reasons_by_name.items()]
    return {
        "trusted_only_ready": bool(report.get("trusted_only_ready")),
        "deck_review_eligible_possible": bool(report.get("deck_review_eligible_possible")),
        "fully_playable_cards": report.get("fully_playable_cards"),
        "partial_cards": report.get("partial_cards"),
        "unresolved_cards": report.get("unresolved_cards"),
        "oracle_residual_gate_pass": bool(report.get("oracle_residual_gate_pass")),
        "data_integrity_issue": has_integrity_blocker(report),
        "data_integrity_blockers": report.get("data_integrity_blockers") or [],
        "card_behavior_ready": bool(report.get("oracle_residual_gate_pass"))
        and report.get("partial_cards") == 0 and report.get("unresolved_cards") == 0,
        "format_verification_pending": any(
            issue.startswith("format_profile:capability_inventory_incomplete:")
            for issue in report.get("strict_binding_blockers", [])
        ),
        "affected_cards": affected[:40],
        "affected_card_count": len(affected),
    }
