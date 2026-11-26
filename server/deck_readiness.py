from __future__ import annotations

from typing import Any, Callable, Iterable

from quorune.card_programs.adapters import CardProgramSourceMismatch
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckLoader
from quorune.preflight import _card_source_hashes, semantic_preflight


_REASON_LABELS = {
    "cast_cost": "Casting cost needs additional rules support",
    "activated_ability": "Activated ability needs additional rules support",
    "triggered_ability": "Triggered ability needs additional rules support",
    "replacement_effect": "Replacement effect needs additional rules support",
    "semantic_source_hash_drift": "The verified rules use different card data",
}


def assess_deck(
    card_db: CardDatabase,
    deck: DeckDefinition,
    *,
    retained_snapshots: Callable[[], Iterable[CardDatabase]] | None = None,
) -> dict[str, Any]:
    """Keep a format-checked list available for review when card data conflicts."""
    try:
        report = semantic_preflight(card_db, deck, collect_source_diagnostics=True)
    except CardProgramSourceMismatch as exc:
        try:
            card = card_db.by_oracle_id(exc.oracle_id).name
        except KeyError:
            card = exc.card_name or "Card requiring a data review"
        report = {"trusted_only_ready": False, "data_integrity_blockers": [exc.diagnostic(card)]}
    blockers = report.get("data_integrity_blockers") or []
    if blockers and retained_snapshots is not None:
        snapshots = tuple(retained_snapshots())
        for blocker in blockers:
            for earlier in snapshots:
                if comparison := _compare_sources(card_db, earlier, blocker):
                    blocker["comparison"] = comparison
                    break
    report["format_legality"] = deck.metadata.get("format_legality", {
        "schema_version": 1, "profile": "commander", "status": "legal", "issues": [],
    })
    return report


def _matches_reviewed_source(
    current: CardDatabase, earlier: CardDatabase, blocker: dict[str, Any],
) -> bool:
    oracle_id = blocker["oracle_id"]
    try:
        active = current.by_oracle_id(oracle_id)
        reviewed = earlier.by_oracle_id(oracle_id)
    except KeyError:
        return False
    active_hashes = _card_source_hashes(current, active)
    reviewed_hashes = _card_source_hashes(earlier, reviewed)
    field = 1 if blocker["kind"] == "rulings_source_mismatch" else 0
    if active_hashes[field] == reviewed_hashes[field]:
        return False
    return any(
        (program["oracle_hash"], program["rulings_hash"]) == reviewed_hashes
        for program in blocker["programs"]
    )


def _compare_sources(
    current: CardDatabase, earlier: CardDatabase, blocker: dict[str, Any],
) -> dict[str, Any] | None:
    if not _matches_reviewed_source(current, earlier, blocker):
        return None
    rulings = blocker["kind"] == "rulings_source_mismatch"
    metadata_key = "scryfall_rulings_updated_at" if rulings else "scryfall_oracle_updated_at"
    return {
        "reviewed_date": earlier.metadata().get(metadata_key),
        "current_date": current.metadata().get(metadata_key),
        "changes": current.source_text_changes(earlier, blocker["oracle_id"], rulings=rulings),
    }


def reviewed_match_snapshot(
    current: CardDatabase,
    snapshots: Iterable[CardDatabase],
    decks: Iterable[DeckDefinition],
) -> CardDatabase | None:
    """Find one retained data source that passes the normal checks for the entire table."""
    lists = tuple(decks)
    if not all(_deck_format_matches(current, deck) for deck in lists):
        return None
    blockers = [
        issue for deck in lists for issue in assess_deck(current, deck).get("data_integrity_blockers", [])
    ]
    if not blockers:
        return None
    for snapshot in snapshots:
        if not all(_matches_reviewed_source(current, snapshot, issue) for issue in blockers):
            continue
        if all(
            _deck_format_matches(snapshot, deck) and assess_deck(snapshot, deck).get("compatibility_ready")
            for deck in lists
        ):
            return snapshot
    return None


def _deck_format_matches(snapshot: CardDatabase, deck: DeckDefinition) -> bool:
    try:
        # A name missing from the earlier snapshot must not resolve to a different card.
        for entry in deck.entries:
            snapshot.lookup(entry.name, fuzzy=False)
        loader = DeckLoader(snapshot)
        expected_issues = (deck.metadata.get("format_legality") or {}).get("issues", [])
        if loader.validate_commander_deck(deck, check_legality=False):
            return False
        return loader.commander_legality_issues(deck) == expected_issues
    except KeyError:
        return False


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
    blockers = report.get("data_integrity_blockers") or []
    mismatched = {issue["card"] for issue in blockers}
    for row in report.get("cards", []):
        if row.get("name") in mismatched:
            continue
        if result := _affected_card(row):
            reasons = reasons_by_name.setdefault(result["name"], [])
            reasons.extend(reason for reason in result["reasons"] if reason not in reasons)
    affected = [{"name": name, "reasons": reasons} for name, reasons in reasons_by_name.items()]
    return {
        "card_rules_ready": bool(report.get("card_rules_ready")),
        "trusted_only_ready": bool(report.get("trusted_only_ready")),
        "deck_review_eligible_possible": bool(report.get("deck_review_eligible_possible")),
        "fully_playable_cards": report.get("fully_playable_cards"),
        "partial_cards": report.get("partial_cards"),
        "unresolved_cards": report.get("unresolved_cards"),
        "oracle_residual_gate_pass": bool(report.get("oracle_residual_gate_pass")),
        "data_integrity_issue": has_integrity_blocker(report),
        "data_integrity_blockers": blockers,
        "card_behavior_ready": bool(report.get("oracle_residual_gate_pass"))
        and report.get("partial_cards") == 0 and report.get("unresolved_cards") == 0,
        "format_verification_pending": any(
            issue.startswith("format_profile:capability_inventory_incomplete:")
            for issue in report.get("strict_binding_blockers", [])
        ),
        "affected_cards": affected[:40],
        "affected_card_count": len(affected),
    }
