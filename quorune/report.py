from __future__ import annotations

import copy
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .engine import CommanderEngine
from .model import Event
from .preflight import card_semantic_status

MEANINGFUL_CODES = {
    "mulligan.keep.private",
    "card.draw.private",
    "land.play",
    "stack.cast",
    "stack.activate",
    "stack.trigger",
    "stack.resolve",
    "stack.counter",
    "library.search",
    "cleanup.discard",
    "combat.attack",
    "combat.block",
    "combat.damage",
    "player.eliminated",
    "game.win",
    "game.draw",
    "action.rejected",
    "state.creatures_died",
    "state.attachments_unattached",
    "state.counters_annihilated",
    "state.objects_ceased",
    "state.world_rule",
    "effect.linked_object_missing",
    "effect.damage",
    "effect.life",
    "effect.energy",
    "token.create",
    "turn.extra.scheduled",
}
LEGACY_PLACEHOLDERS = {
    "",
    "unavailable",
    "unavailable in v2 record",
    "unknown",
    "not recorded",
}

def _card_by_ref(engine: CommanderEngine) -> dict[str, Any]:
    return {card.ref: card for card in engine.state.cards.values()}


def _name(engine: CommanderEngine, ref: str) -> str:
    card = _card_by_ref(engine).get(ref)
    return card.printed_name if card else ref


def _oracle_name(engine: CommanderEngine, oracle_id: str) -> str:
    try:
        return engine.card_db.by_oracle_id(oracle_id).name
    except KeyError:
        return oracle_id


def _format_mana(bundle: Mapping[str, Any] | None) -> str:
    values = dict(bundle or {})
    symbols: list[str] = []
    generic = int(values.get("GENERIC", 0))
    if generic:
        symbols.append(f"{{{generic}}}")
    for color in ("W", "U", "B", "R", "G", "C"):
        symbols.extend(f"{{{color}}}" for _ in range(int(values.get(color, 0))))
    return "".join(symbols) or "no mana"


def _opening_hands(engine: CommanderEngine) -> dict[str, dict[str, Any]]:
    result = {
        seat: {"kept": None, "cards": [], "mulligans": engine.state.players[seat].mulligans_taken}
        for seat in engine.state.turn_order
    }
    for event in engine.state.events:
        if event.code != "mulligan.keep.private" or event.actor not in result:
            continue
        refs = list(event.details.get("objects") or [])
        result[event.actor] = {
            "kept": len(refs),
            "cards": [{"id": ref, "name": _name(engine, ref)} for ref in refs],
            "mulligans": engine.state.players[event.actor].mulligans_taken,
            "visibility": "analyst_only",
        }
    return result


def _land_entry_review(engine: CommanderEngine) -> dict[str, Any]:
    conflicts: list[dict[str, Any]] = []
    plays: list[dict[str, Any]] = []
    by_ref = _card_by_ref(engine)
    for event in engine.state.events:
        if event.code != "land.play" or event.actor is None:
            continue
        ref = str(event.details.get("object"))
        card = by_ref.get(ref)
        record = engine.card_record(card) if card else None
        if record is None:
            continue
        actual = bool(event.details.get("tapped", False))
        life_paid = event.details.get("life_paid", 0)
        valid_result = type(event.details.get("tapped", False)) is bool and (
            type(life_paid) is int and life_paid >= 0
        )
        row = {
            "turn": event.turn_sequence,
            "seat": event.actor,
            "id": ref,
            "name": record.name,
            "recorded_tapped": actual,
            "expected_tapped": actual if valid_result else None,
            "basis": "canonical typed land-entry journal",
            "life_paid": life_paid,
        }
        plays.append(row)
        if not valid_result:
            conflicts.append(row)
    return {
        "plays": len(plays),
        "all_recorded_tapped": bool(plays) and all(row["recorded_tapped"] for row in plays),
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
    }


def _is_typed_library_search_activation(
    engine: CommanderEngine,
    event: Event,
    by_ref: Mapping[str, Any],
) -> bool:
    if event.code != "stack.activate":
        return False
    source = by_ref.get(str(event.details.get("source") or ""))
    ability_id = str(event.details.get("ability") or "")
    if source is None or not ability_id:
        return False
    return any(
        ability.ability_id == ability_id and bool(ability.library_search_types)
        for ability in engine._activated_abilities(source)
    )


def _semantic_coverage(engine: CommanderEngine) -> dict[str, Any]:
    refs: dict[str, set[str]] = defaultdict(set)
    by_ref = _card_by_ref(engine)
    for event in engine.state.events:
        if event.code in {"land.play", "stack.cast", "stack.activate"}:
            value = event.details.get("object") or event.details.get("source")
            if value:
                refs[str(value)].add(event.code)
    unresolved = []
    partial = []
    cards: list[dict[str, Any]] = []
    for ref in sorted(refs):
        card = by_ref.get(ref)
        record = engine.card_record(card) if card else None
        operations = refs[ref]
        if not record:
            continue
        registered_programs = engine.semantics.programs_for_oracle(record.oracle_id)
        trust = engine.semantics.trust_for_oracle(record.oracle_id)
        trusted_programs = [
            program
            for program in registered_programs
            if program.trust_level == "trusted"
        ]
        source_hash_match = all(
            engine.semantic_program_is_current_trusted(program)
            for program in trusted_programs
        )
        if trusted_programs and not source_hash_match:
            trust = "unresolved"
        preflight_status = card_semantic_status(
            record,
            engine.semantics,
            db=engine.card_db,
        )
        preflight_fully_playable = (
            preflight_status["status"] == "fully_playable"
        )
        builtin_fetch = any(
            str(event.details.get("source") or "") == ref
            and _is_typed_library_search_activation(engine, event, by_ref)
            for event in engine.state.events
        )
        builtin_land_entry = operations == {"land.play"} and any(
            handler.get("handler_id")
            == "replacement.zone.entry-state.v1"
            for program in trusted_programs
            if engine.semantic_program_is_current_trusted(program)
            for handler in program.handlers
        )
        if (
            preflight_fully_playable
            or trust == "trusted"
            or ("stack.activate" in operations and builtin_fetch)
            or builtin_land_entry
        ):
            status = "fully_supported"
            reason = "trusted semantic pack or built-in semantics"
        elif trust == "intentionally_ignored":
            status = "intentionally_ignored_as_irrelevant"
            reason = "semantic pack marks the encountered text intentionally irrelevant"
        elif registered_programs and trust == "provisional":
            status = "partially_supported"
            reason = "only provisional semantic programs covered this card"
        elif registered_programs and trust == "unresolved":
            status = "unresolved"
            reason = "semantic pack explicitly marks relevant behavior unresolved"
        elif preflight_status["status"] == "partial":
            status = "partially_supported"
            reason = "typed semantic preflight reports partial support"
        else:
            status = "unresolved"
            reason = "typed semantic authority did not certify the observed operation"
        effective_trust = trust
        if status == "fully_supported" and (
            preflight_fully_playable
            or builtin_fetch
            or builtin_land_entry
            or operations == {"land.play"}
        ):
            effective_trust = "trusted"
        elif status == "intentionally_ignored_as_irrelevant":
            effective_trust = "intentionally_ignored"
        row = {
            "id": ref,
            "name": record.name,
            "operations": sorted(operations),
            "status": status,
            "reason": reason,
            "trust_level": effective_trust,
            "source_hash_match": source_hash_match,
            "semantic_programs": [
                {
                    "key": program.key,
                    "version": program.version,
                    "trust_level": program.trust_level,
                    "source_hash_match": (
                        engine.semantic_program_is_current_trusted(program)
                        if program.trust_level == "trusted"
                        else None
                    ),
                }
                for program in registered_programs
            ],
        }
        cards.append(row)
        if status == "unresolved":
            unresolved.append(row)
        elif status == "partially_supported":
            partial.append(row)
    return {
        "status": "complete" if not unresolved and not partial else "partial",
        "cards": cards,
        "partially_supported": partial,
        "unresolved_relevant": unresolved,
    }


def _event_description(engine: CommanderEngine, event: Event) -> str:
    details = event.details
    seat = event.actor or event.active_player or "System"
    if event.code == "mulligan.keep":
        return f"{seat} kept their opening hand."
    if event.code == "mulligan.keep.private":
        names = ", ".join(_name(engine, str(ref)) for ref in details.get("objects") or [])
        return f"{seat} kept {len(details.get('objects') or [])}: {names}."
    if event.code == "card.draw.private":
        names = ", ".join(_name(engine, str(ref)) for ref in details.get("objects") or [])
        return f"{seat} drew {names or details.get('count', 1)}."
    if event.code == "land.play":
        ref = str(details.get("object") or "")
        suffix = " tapped" if details.get("tapped") else " untapped"
        paid = (
            f" after paying {details.get('life_paid')} life"
            if details.get("life_paid")
            else ""
        )
        return f"{seat} played {_name(engine, ref)}{suffix}{paid}."
    if event.code == "stack.cast":
        ref = str(details.get("object") or "")
        payment = details.get("payment") or {}
        sources = details.get("mana_sources") or []
        paid = (
            " using "
            + ", ".join(
                f"{_name(engine, str(item.get('source')))} for "
                f"{_format_mana(item.get('bundle'))}"
                for item in sources
                if item.get("source")
            )
            if sources
            else ""
        )
        tax = (
            f" (commander tax {details.get('commander_tax')})"
            if details.get("commander_tax")
            else ""
        )
        target = (
            f", targeting {', '.join(_name(engine, str(value)) for value in details.get('targets') or [])}"
            if details.get("targets")
            else ""
        )
        modes = (
            f", modes {', '.join(map(str, details.get('modes') or []))}"
            if details.get("modes")
            else ""
        )
        x_value = (
            f", X={details.get('x')}"
            if details.get("x") is not None
            else ""
        )
        return (
            f"{seat} cast {_name(engine, ref)} from "
            f"{details.get('from', 'an unknown zone')} for "
            f"{_format_mana(payment)}{paid}{tax}{target}{modes}{x_value}."
        )
    if event.code == "stack.activate":
        ref = str(details.get("source") or "")
        payment = details.get("payment") or {}
        life_paid = int(details.get("life_paid") or 0)
        if not life_paid:
            source = _card_by_ref(engine).get(ref)
            if source is not None:
                life_paid = next(
                    (
                        ability.life_payment
                        for ability in engine._activated_abilities(source)
                        if ability.ability_id == details.get("ability")
                    ),
                    0,
                )
        costs = [
            _name(engine, str(value))
            for value in details.get("cost_objects") or []
        ]
        suffix = []
        if payment:
            suffix.append(f"paid {_format_mana(payment)}")
        if life_paid:
            suffix.append(f"paid {life_paid} life")
        if costs:
            suffix.append(f"used {', '.join(costs)} as costs")
        if details.get("targets"):
            suffix.append(
                "targeted "
                + ", ".join(
                    _name(engine, str(value))
                    for value in details["targets"]
                )
            )
        if details.get("modes"):
            suffix.append(f"chose modes {', '.join(map(str, details['modes']))}")
        return (
            f"{seat} activated {_name(engine, ref)} "
            f"({details.get('ability')})"
            f"{'; ' + '; '.join(suffix) if suffix else ''}."
        )
    if event.code == "stack.resolve":
        if details.get("note") == "Automatic vanilla/default resolution":
            return (
                f"Resolved {details.get('stack')} as a permanent spell; "
                "no entry trigger applied."
            )
        return f"Resolved {details.get('stack')} ({details.get('note') or 'registered/default semantics'})."
    if event.code == "stack.counter":
        return f"{details.get('stack')} was countered ({details.get('reason')})."
    if event.code == "library.search":
        ref = details.get("object")
        entry = "tapped" if details.get("tapped") else "untapped"
        life_suffix = (
            f" after paying {details.get('life_paid')} life"
            if details.get("life_paid")
            else ""
        )
        return (
            f"{seat} searched for {_name(engine, str(ref))}; it entered "
            f"{entry}{life_suffix}."
            if ref
            else f"{seat} searched and did not find a card."
        )
    if event.code == "cleanup.discard":
        refs = list(details.get("objects") or [])
        names = ", ".join(_name(engine, str(ref)) for ref in refs)
        return f"{seat} discarded {names or len(refs)} to maximum hand size."
    if event.code == "combat.attack":
        attackers = details.get("attackers") or {}
        if not attackers:
            return f"{seat} declared no attackers."
        values = ", ".join(
            f"{_name(engine, str(ref))} at {defender}"
            for ref, defender in attackers.items()
        )
        return f"{seat} attacked with {values}."
    if event.code == "combat.block":
        blocks = details.get("blocks") or {}
        values = ", ".join(
            f"{_name(engine, str(blocker))} blocked {_name(engine, str(attacker))}"
            for blocker, attacker in blocks.items()
        )
        return f"{seat} declared {values or str(len(blocks)) + ' block(s)'}."
    if event.code == "combat.damage":
        values = ", ".join(
            f"{_name(engine, str(item.get('source')))} dealt {item.get('amount')} to {item.get('target')}"
            for item in details.get("assignments") or []
        )
        return values + "."
    if event.code == "player.eliminated":
        return f"{seat} was eliminated ({details.get('reason')})."
    if event.code == "game.win":
        return f"{seat} won the game."
    if event.code == "game.draw":
        return "The game ended in a draw."
    if event.code == "action.rejected":
        return f"{seat}'s action was rejected: {details.get('reason') or 'unknown reason'}."
    if event.code == "state.creatures_died":
        names = ", ".join(
            _name(engine, str(ref)) for ref in details.get("objects") or []
        )
        return f"{names or 'Permanents'} died or were put into graveyards."
    if event.code == "state.attachments_unattached":
        names = ", ".join(
            _name(engine, str(ref))
            for ref in details.get("objects") or []
        )
        return (
            f"{names or 'Permanents'} became unattached due to "
            "state-based actions."
        )
    if event.code == "state.counters_annihilated":
        values = ", ".join(
            (
                f"{_name(engine, str(item.get('object')))} "
                f"({item.get('pairs_removed')} pair(s))"
            )
            for item in details.get("changes") or []
        )
        return (
            "Opposing +1/+1 and -1/-1 counters were removed"
            + (f": {values}." if values else ".")
        )
    if event.code == "state.objects_ceased":
        values = ", ".join(
            (
                f"{_name(engine, str(item.get('object')))} "
                f"from {item.get('zone')}"
            )
            for item in details.get("objects") or []
        )
        return (
            "Token or copy objects ceased to exist"
            + (f": {values}." if values else ".")
        )
    if event.code == "state.world_rule":
        moved = ", ".join(
            _name(engine, str(item.get("object")))
            for item in details.get("moved") or []
        )
        survivors = ", ".join(
            _name(engine, str(ref))
            for ref in details.get("survivors") or []
        )
        return (
            "The world rule moved "
            + (moved or "the tied World permanents")
            + " to their owners' graveyards"
            + (f"; {survivors} remained." if survivors else ".")
        )
    if event.code == "effect.linked_object_missing":
        return (
            f"{_name(engine, str(details.get('object')))} was no "
            "longer the object linked by the resolving effect."
        )
    if event.code == "token.create":
        names = ", ".join(
            _name(engine, str(ref)) for ref in details.get("objects") or []
        )
        return f"{seat} created {names or 'token(s)'}."
    if event.code == "effect.damage":
        return (
            f"{details.get('target')} took {details.get('amount')} damage "
            f"({details.get('reason') or 'effect'})."
        )
    if event.code == "effect.life":
        return event.summary
    if event.code == "effect.energy":
        return f"{details.get('player')} changed energy by {details.get('delta')}."
    if event.code == "turn.extra.scheduled":
        return f"{seat} received an extra turn."
    return event.summary if event.summary != event.code else event.code


def _turn_groups(
    events: Sequence[Event],
    engine: CommanderEngine,
) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    active: dict[int, str | None] = {}
    for event in events:
        if event.code not in MEANINGFUL_CODES:
            continue
        if (
            event.code == "card.draw.private"
            and event.details.get("reason") == "opening hand"
        ):
            continue
        if event.code == "combat.damage" and not event.details.get("assignments"):
            continue
        if event.code == "combat.attack" and not event.details.get("attackers"):
            continue
        if event.code == "combat.block" and not event.details.get("blocks"):
            continue
        grouped[event.turn_sequence].append(
            {
                "event_id": event.event_id,
                "phase": event.phase,
                "step": event.step,
                "actor": event.actor,
                "code": event.code,
                "summary": _event_description(engine, event),
                "details": event.details,
            }
        )
        active[event.turn_sequence] = event.active_player
    return [
        {"turn": turn, "active_player": active.get(turn), "events": grouped[turn]}
        for turn in sorted(grouped)
    ]


def derive_review(
    engine: CommanderEngine,
    *,
    decisions: Sequence[Mapping[str, Any]] = (),
    manifest: Mapping[str, Any] | None = None,
    record_directory: str | Path | None = None,
) -> dict[str, Any]:
    state = engine.state
    counts = Counter(event.code for event in state.events)
    casts: dict[str, list[dict[str, Any]]] = {seat: [] for seat in state.turn_order}
    land_counts = Counter()
    discards = Counter()
    damage = Counter()
    attacks = Counter()
    draws: dict[str, dict[str, Any]] = {
        seat: {"lands": 0, "spells": 0, "cards": []}
        for seat in state.turn_order
    }
    mana_by_turn: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    mana_spent_by_turn: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    mana_unused_by_turn: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    commander_casts = Counter()
    tutors: list[dict[str, Any]] = []
    by_ref = _card_by_ref(engine)
    for event in state.events:
        if event.code == "stack.cast" and event.actor in casts:
            ref = str(event.details.get("object"))
            casts[event.actor].append(
                {"turn": event.turn_sequence, "id": ref, "name": _name(engine, ref)}
            )
            for color, amount in (event.details.get("payment") or {}).items():
                mana_spent_by_turn[event.turn_sequence][color] += int(amount)
            if event.details.get("from") == "command":
                commander_casts[event.actor] += 1
        elif event.code == "land.play" and event.actor:
            land_counts[event.actor] += 1
        elif event.code == "cleanup.discard" and event.actor:
            discards[event.actor] += len(event.details.get("objects") or [])
        elif event.code == "combat.attack" and event.actor:
            attacks[event.actor] += len(event.details.get("attackers") or [])
        elif event.code == "combat.damage":
            for assignment in event.details.get("assignments") or []:
                target = assignment.get("target")
                if target in state.players:
                    damage[target] += int(assignment.get("amount", 0))
        elif event.code == "card.draw.private" and event.actor in draws:
            if str(event.details.get("reason") or "") == "opening hand":
                continue
            refs = list(event.details.get("objects") or [])
            for ref in refs:
                card = by_ref.get(str(ref))
                record = engine.card_record(card) if card else None
                kind = "lands" if record and record.is_land else "spells"
                draws[event.actor][kind] += 1
                draws[event.actor]["cards"].append(
                    {
                        "turn": event.turn_sequence,
                        "id": ref,
                        "name": card.printed_name if card else str(ref),
                        "kind": kind[:-1],
                    }
                )
        elif event.code in {"mana.produce", "mana.ability"} and event.actor:
            for color, amount in (event.details.get("bundle") or {}).items():
                mana_by_turn[event.turn_sequence][color] += int(amount)
        elif event.code == "mana.empty" and event.actor:
            for color, amount in (event.details.get("lost") or {}).items():
                mana_unused_by_turn[event.turn_sequence][color] += int(amount)
        elif event.code == "library.search":
            ref = event.details.get("object")
            tutors.append(
                {
                    "turn": event.turn_sequence,
                    "seat": event.actor,
                    "selected": (
                        {"id": ref, "name": _name(engine, str(ref))}
                        if ref
                        else None
                    ),
                    "source": event.details.get("source"),
                }
            )

    land_review = _land_entry_review(engine)
    semantics = _semantic_coverage(engine)
    legacy_decisions = any(bool(row.get("legacy_incomplete")) for row in decisions)
    smoke_marker = any(
        "smoke" in str(event.details.get("note") or "").casefold()
        for event in state.events
        if event.code == "stack.resolve"
    )
    replay_status = (
        str((manifest or {}).get("replay", {}).get("verification") or "not_run")
    )
    opportunities = copy.deepcopy(state.action_opportunities)
    optimization_totals = {
        key: sum(
            int(
                state.players[seat]
                .stats.get("decision_optimization", {})
                .get(key, 0)
            )
            for seat in state.turn_order
        )
        for key in (
            "priority_windows_considered",
            "pass_only_windows_skipped",
            "yield_covered_windows",
            "suppressed_empty_windows",
            "suppressed_meaningful_windows",
            "yields_invalidated_by_phase",
            "yields_invalidated_by_draw",
            "yields_invalidated_by_action_change",
            "yields_invalidated_by_stack",
            "yields_invalidated_by_public_change",
            "illegal_target_actions_prevented",
            "illegal_target_actions_advertised",
            "actions_removed_for_no_targets",
            "actions_removed_for_mode_target_failure",
            "target_candidates_generated",
            "target_submissions_rejected",
            "targets_became_illegal_on_resolution",
            "spells_countered_by_rules",
            "spells_countered_by_effect",
            "stack_interaction_windows_created",
            "stack_interaction_windows_auto_passed",
        )
    }
    suppressed_meaningful = max(
        optimization_totals["suppressed_meaningful_windows"],
        sum(
            bool(row.get("incorrectly_suppressed"))
            and bool(row.get("meaningful_actions_exist"))
            for row in opportunities
        ),
    )
    meaningful_opportunities = [
        row for row in opportunities if row.get("meaningful_actions_exist")
    ]
    uncovered_opportunities = [
        row
        for row in meaningful_opportunities
        if row.get("incorrectly_suppressed")
        or row.get("outcome")
        not in {
            "pilot_task_issued",
            "safe_yield",
            "ordered_plan",
        }
    ]
    opportunity_coverage = (
        "unavailable"
        if not opportunities
        else "fail"
        if suppressed_meaningful or uncovered_opportunities
        else "pass"
    )
    arena = dict((manifest or {}).get("codex_arena") or {})
    stop_reason = dict(
        arena.get("stop_reason")
        or (manifest or {}).get("pause_reason")
        or {}
    )
    legal_exposure_stop = (
        stop_reason.get("kind") == "legal_action_exposure_failure"
    )
    illegal_target_exposure = (
        optimization_totals["illegal_target_actions_advertised"] > 0
    )
    manifest_deck_fingerprints = [
        str(
            player.get("deck_list_fingerprint")
            or player.get("deck_fingerprint")
            or ""
        )
        for player in (manifest or {}).get("players", [])
    ]
    duplicated_deck_fixture = (
        len(manifest_deck_fingerprints) == len(state.turn_order)
        and len(set(manifest_deck_fingerprints)) < len(state.turn_order)
    )
    profile_match_value = (manifest or {}).get(
        "profile_fingerprint_match", "unavailable"
    )
    fidelity_failures = []
    if land_review["conflict_count"]:
        fidelity_failures.append("land-entry conflicts")
    if semantics["status"] != "complete":
        fidelity_failures.append("incomplete relevant Oracle semantics")
    decision_groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for index, row in enumerate(decisions):
        key = str(row.get("decision_id") or f"attempt:{index}")
        decision_groups[key].append(row)
    complete_alternatives = (
        bool(decision_groups)
        and not legacy_decisions
        and all(
            any(
                isinstance(row.get("legal_alternatives"), list)
                and bool(row.get("legal_alternatives"))
                for row in rows
            )
            for rows in decision_groups.values()
        )
    )
    accepted_rows = [
        row
        for row in decisions
        if row.get("accepted") and row.get("role") == "pilot"
    ]
    complete_reasons = (
        bool(accepted_rows)
        and not legacy_decisions
        and all(
            isinstance(row.get("reason"), str)
            and row.get("reason", "").strip().casefold()
            not in LEGACY_PLACEHOLDERS
            for row in accepted_rows
        )
    )
    if not complete_alternatives or not complete_reasons:
        fidelity_failures.append("incomplete pilot decision alternatives/reasons")
    if opportunity_coverage == "unavailable":
        fidelity_failures.append(
            "historical action-opportunity coverage unavailable"
        )
    elif opportunity_coverage == "fail":
        fidelity_failures.append("action-opportunity coverage failed")
    if suppressed_meaningful:
        fidelity_failures.append(
            f"{suppressed_meaningful} meaningful decision window(s) were suppressed"
        )
    if legal_exposure_stop:
        fidelity_failures.append(
            "legal-action exposure failed at the recorded arena stop boundary"
        )
    if illegal_target_exposure:
        fidelity_failures.append(
            f"{optimization_totals['illegal_target_actions_advertised']} action(s) "
            "were advertised without a legal mandatory target"
        )
    if profile_match_value is False:
        fidelity_failures.append("pilot profile deck-list fingerprint mismatch")
    if duplicated_deck_fixture:
        fidelity_failures.append(
            "duplicated-deck protocol fixture is not matchup evidence"
        )
    if replay_status not in {"pass", "snapshot_only"}:
        fidelity_failures.append("replay verification not established")
    if smoke_marker:
        fidelity_failures.append("fixture explicitly identifies itself as a smoke baseline")
    if state.config.effective_profile(len(state.turn_order)) not in {
        "commander_duel",
        "commander_multiplayer",
    }:
        fidelity_failures.append("format profile mismatch")
    arena_disqualifiers = [
        bool(arena.get("primary_made_strategic_decision")),
        arena.get("persistent_thread_reuse") is False,
        arena.get("seat_projection_verified") is False,
        arena.get("provider_identity_verified") is False,
    ]
    if suppressed_meaningful or legal_exposure_stop or illegal_target_exposure:
        classification = "rules_test"
    elif not state.game_over:
        classification = (
            "pilot_test"
            if decisions and not legacy_decisions and not smoke_marker
            else "in_progress"
        )
    elif smoke_marker or legacy_decisions or not decisions:
        classification = "smoke_only"
    elif fidelity_failures:
        classification = "pilot_test"
    else:
        classification = "deck_review_eligible"
    if classification == "pilot_test" and any(arena_disqualifiers):
        classification = "rules_test"
    if duplicated_deck_fixture and classification == "deck_review_eligible":
        classification = "pilot_test"
    eligible = classification == "deck_review_eligible" and not fidelity_failures
    deck_operation_failures: list[str] = []
    if state.config.semantic_policy != "trusted_only":
        deck_operation_failures.append(
            "semantic_policy is not trusted_only"
        )
    if state.config.effective_profile(len(state.turn_order)) != (
        "commander_multiplayer"
    ):
        deck_operation_failures.append(
            "format is not four-player Commander multiplayer"
        )
    if len(state.turn_order) != 4:
        deck_operation_failures.append(
            "operation evidence requires exactly four seats"
        )
    if not state.game_over or not (state.winner or state.draw):
        deck_operation_failures.append(
            "game did not reach a natural winner or draw"
        )
    if semantics["status"] != "complete":
        deck_operation_failures.append(
            "encountered semantics were not fully trusted"
        )
    if replay_status != "pass":
        deck_operation_failures.append(
            "exact command replay did not pass"
        )
    if opportunity_coverage != "pass" or suppressed_meaningful:
        deck_operation_failures.append(
            "action-opportunity fidelity did not pass"
        )
    if illegal_target_exposure or legal_exposure_stop:
        deck_operation_failures.append(
            "legal-action exposure did not pass"
        )
    if land_review["conflict_count"]:
        deck_operation_failures.append(
            "rules-kernel conflicts were recorded"
        )
    if not complete_alternatives or not complete_reasons:
        deck_operation_failures.append(
            "pilot decision audit is incomplete"
        )
    if profile_match_value is not True:
        deck_operation_failures.append(
            "exact profile fingerprints were not verified"
        )
    if arena.get("pilot_thread_count") != 4:
        deck_operation_failures.append(
            "exactly four persistent pilot threads were not verified"
        )
    if arena.get("persistent_thread_reuse") is not True:
        deck_operation_failures.append(
            "persistent pilot-thread reuse was not verified"
        )
    if arena.get("primary_made_strategic_decision") is not False:
        deck_operation_failures.append(
            "the primary coordinator made a strategic seat decision"
        )
    if arena.get("seat_projection_verified") is not True:
        deck_operation_failures.append(
            "seat projection was not verified"
        )
    if arena.get("provider_identity_verified") is not True:
        deck_operation_failures.append(
            "pilot provider identity was not verified"
        )
    if arena.get("model_identity_verified") is not True:
        deck_operation_failures.append(
            "pilot model identity was not verified"
        )
    if arena.get("codex_subagent_run") is not True:
        deck_operation_failures.append(
            "no actual Codex-subagent run was verified"
        )
    if stop_reason and stop_reason.get("kind") not in {
        "complete",
        "game_complete",
        "winner",
        "draw",
    }:
        deck_operation_failures.append(
            "an infrastructure or fidelity stop was recorded"
        )
    deck_operation_evidence = not deck_operation_failures
    if deck_operation_evidence:
        classification = "deck_operation_evidence"
        eligible = True

    turns_begun = {seat: state.players[seat].turns_begun for seat in state.turn_order}
    accepted = sum(bool(row.get("accepted")) for row in decisions)
    rejected = len(decisions) - accepted
    pass_decisions = sum(row.get("action") == "pass" for row in decisions)
    turn_groups = _turn_groups(state.events, engine)
    groups_by_turn = {group["turn"]: group for group in turn_groups}
    for row in decisions:
        if not row.get("accepted") or row.get("action") == "pass":
            continue
        group = groups_by_turn.get(int(row.get("turn", 0)))
        if group is None:
            continue
        group.setdefault("decisions", []).append(
            {
                "seat": row.get("seat") or row.get("actor"),
                "action_id": row.get("action_id"),
                "plan": row.get("plan_category"),
                "reason": row.get("reason"),
                "legacy_incomplete": bool(row.get("legacy_incomplete")),
                "legal_alternatives_recorded": isinstance(
                    row.get("legal_alternatives"), list
                ),
            }
        )
    for opportunity in opportunities:
        if not opportunity.get("meaningful_actions_exist"):
            continue
        group = groups_by_turn.get(
            int(opportunity.get("turn_sequence", 0))
        )
        if group is None:
            continue
        action_ids = list(
            opportunity.get("meaningful_action_ids") or []
        )
        land_names = [
            _name(engine, action_id.split(":", 1)[1])
            for action_id in action_ids
            if str(action_id).startswith("play-land:")
        ]
        cast_names = [
            _name(engine, action_id.split(":", 1)[1])
            for action_id in action_ids
            if str(action_id).startswith("cast:")
        ]
        delivered_decision = next(
            (
                row
                for row in decisions
                if str(row.get("decision_id"))
                == str(opportunity.get("decision_id"))
                and row.get("accepted")
            ),
            None,
        )
        group.setdefault("action_opportunities", []).append(
            {
                "seat": opportunity.get("seat"),
                "phase": opportunity.get("phase"),
                "step": opportunity.get("step"),
                "action_signature": opportunity.get("action_signature"),
                "yield_invalidated_by": opportunity.get(
                    "yield_invalidated_by"
                ),
                "legal_land_plays": land_names,
                "legal_casts": cast_names,
                "outcome": opportunity.get("outcome"),
                "incorrectly_suppressed": bool(
                    opportunity.get("incorrectly_suppressed")
                ),
                "chosen_action_id": (
                    delivered_decision.get("action_id")
                    if delivered_decision
                    else None
                ),
                "plan": (
                    delivered_decision.get("plan_category")
                    if delivered_decision
                    else None
                ),
            }
        )
    first_three: dict[str, list[dict[str, Any]]] = {}
    for seat in state.turn_order:
        seat_turns = [
            group for group in turn_groups
            if group["turn"] and group["active_player"] == seat
        ][:3]
        first_three[seat] = seat_turns
    legal_action_trace_complete = (
        complete_alternatives
        and complete_reasons
        and opportunity_coverage == "pass"
    )
    stranded = {
        seat: [
            {
                "id": state.cards[object_id].ref,
                "name": state.cards[object_id].printed_name,
                "why": "The game ended while the card remained in hand; strategic causality is not inferred.",
            }
            for object_id in state.players[seat].zones["hand"]
        ]
        for seat in state.turn_order
    }
    opportunity_by_decision = {
        str(row.get("decision_id")): row
        for row in opportunities
        if row.get("decision_id")
    }
    suspected_pilot: list[dict[str, Any]] = []
    for row in decisions:
        if not row.get("accepted") or row.get("action") != "pass":
            continue
        opportunity = opportunity_by_decision.get(
            str(row.get("decision_id"))
        )
        if not opportunity or not opportunity.get(
            "meaningful_actions_exist"
        ):
            continue
        suspected_pilot.append(
            {
                "seat": row.get("seat") or row.get("actor"),
                "turn": row.get("turn"),
                "phase": row.get("phase"),
                "finding": (
                    "Pilot chose to pass in this exact delivered window despite "
                    "at least one verified, currently payable action."
                ),
                "confidence": "verified_delivery",
                "legal_alternatives_verified": True,
                "action_signature": opportunity.get("action_signature"),
                "action_ids": opportunity.get("meaningful_action_ids", []),
                "caveat": None,
            }
        )
    replay_pass = replay_status in {"pass", "snapshot_only"}
    dimensions = {
        "format_match": "pass",
        "semantic_policy": state.config.semantic_policy,
        "deck_operation_evidence": deck_operation_evidence,
        "rules_kernel": "fail" if land_review["conflict_count"] else "partial",
        "card_semantics": "pass" if semantics["status"] == "complete" else "fail",
        "pilot_trace": "pass" if legal_action_trace_complete else "fail",
        "legal_action_exposure": (
            "fail"
            if (
                suppressed_meaningful
                or legal_exposure_stop
                or illegal_target_exposure
            )
            else "pass"
            if legal_action_trace_complete
            else "unavailable"
        ),
        "profile_fingerprint_match": (
            "pass"
            if profile_match_value is True
            else "fail"
            if profile_match_value is False
            else "unavailable"
        ),
        "action_opportunity_coverage": opportunity_coverage,
        "hidden_information": "pass",
        "replay_verification": "pass" if replay_pass else "fail",
        "pilot_thread_count": arena.get("pilot_thread_count"),
        "persistent_thread_reuse": arena.get(
            "persistent_thread_reuse", "not_applicable"
        ),
        "primary_made_strategic_decision": arena.get(
            "primary_made_strategic_decision", False
        ),
        "provider_identity_verified": arena.get(
            "provider_identity_verified", "not_applicable"
        ),
        "model_identity_verified": arena.get(
            "model_identity_verified", "not_applicable"
        ),
        "seat_projection_verified": arena.get(
            "seat_projection_verified", "not_applicable"
        ),
        "codex_subagent_run": arena.get("codex_subagent_run", False),
        "ordered_plan_responses": int(
            (manifest or {})
            .get("provider_telemetry", {})
            .get("ordered_plans_submitted", 0)
        ),
        "arena_stop_reason": stop_reason or None,
    }
    provider_rows = [
        row for row in decisions if row.get("provider_invoked") is True
    ]
    pilot_provider_rows = [
        row for row in provider_rows if row.get("role") == "pilot"
    ]
    arbiter_provider_rows = [
        row for row in provider_rows if row.get("role") == "arbiter"
    ]
    observed_input = sum(
        int(row.get("metrics", {}).get("input_tokens", 0))
        for row in provider_rows
        if row.get("metrics", {}).get("input_tokens") is not None
    )
    observed_output = sum(
        int(row.get("metrics", {}).get("output_tokens", 0))
        for row in provider_rows
        if row.get("metrics", {}).get("output_tokens") is not None
    )
    measured_invocations = sum(
        row.get("metrics", {}).get("input_tokens") is not None
        and row.get("metrics", {}).get("output_tokens") is not None
        for row in provider_rows
    )
    def observed_role_tokens(
        rows: list[dict[str, Any]], field: str
    ) -> int | None:
        measured = [
            int(row.get("metrics", {}).get(field, 0))
            for row in rows
            if row.get("metrics", {}).get(field) is not None
        ]
        return sum(measured) if measured else None
    if legacy_decisions:
        token_status = "unknown_legacy"
        pilot_invocations: int | None = None
        arbiter_invocations: int | None = None
    else:
        token_status = (
            "complete"
            if provider_rows and measured_invocations == len(provider_rows)
            else "partial"
            if measured_invocations
            else "unavailable"
        )
        pilot_invocations = len(pilot_provider_rows)
        arbiter_invocations = len(arbiter_provider_rows)
    automatic_decisions = 0
    planned_decision_ids: set[str] = set()
    if record_directory:
        command_path = Path(record_directory) / "commands.jsonl"
        if command_path.exists():
            command_rows = [
                json.loads(line)
                for line in command_path.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ]
            automatic_decisions = sum(
                row.get("execution") == "planned_automatic"
                for row in command_rows
            )
            planned_decision_ids = {
                str(row.get("decision_id"))
                for row in command_rows
                if row.get("execution") == "planned_automatic"
                and row.get("decision_id")
            }
    for opportunity in opportunities:
        if str(opportunity.get("decision_id")) in planned_decision_ids:
            opportunity["ordered_plan_covered"] = True

    diagnostic_unpayable = sum(
        len((row.get("diagnostic") or {}).get("unpayable") or [])
        for row in opportunities
    )
    diagnostic_unresolved = sum(
        len(
            (row.get("diagnostic") or {}).get(
                "unresolved_cost_semantics"
            )
            or []
        )
        for row in opportunities
    )
    opportunity_audit = {
        "status": opportunity_coverage,
        "priority_windows_considered": optimization_totals[
            "priority_windows_considered"
        ],
        "journal_rows": len(opportunities),
        "meaningful_windows": len(meaningful_opportunities),
        "pilot_chose_to_pass_with_verified_action": len(
            suspected_pilot
        ),
        "pilot_was_never_asked": sum(
            bool(row.get("meaningful_actions_exist"))
            and bool(row.get("incorrectly_suppressed"))
            for row in opportunities
        ),
        "action_generator_failed_to_expose": sum(
            bool((row.get("diagnostic") or {}).get("generator_failure"))
            for row in opportunities
        ),
        "yield_incorrectly_suppressed": suppressed_meaningful,
        "action_existed_but_was_not_payable": diagnostic_unpayable,
        "semantics_prevented_action_generation": diagnostic_unresolved,
        "note": (
            "Each priority opportunity is joined to its exact action-signature "
            "and disposition."
            if opportunities
            else "This historical record predates the engine-side opportunity "
            "journal. Inactivity is infrastructure-unverified and is not "
            "attributed to a pilot."
        ),
    }

    report: dict[str, Any] = {
        "schema_version": 1,
        "game_id": state.game_id,
        "outcome": {
            "status": str(
                (manifest or {}).get("status")
                or ("complete" if state.game_over else "in_progress")
            ),
            "pause_reason": copy.deepcopy(
                (manifest or {}).get("pause_reason")
            ),
            "winner": state.winner,
            "draw": state.draw,
            "eliminations": [
                {
                    "seat": event.actor,
                    "turn": event.turn_sequence,
                    "reason": event.details.get("reason"),
                }
                for event in state.events
                if event.code == "player.eliminated"
            ],
        },
        "format": {
            "name": state.config.format_name,
            "review_profile": state.config.review_profile,
            "profile": state.config.effective_profile(len(state.turn_order)),
            "mode": (
                "two-player commander_duel"
                if state.config.effective_profile(len(state.turn_order))
                == "commander_duel"
                else "four-player commander_review"
                if len(state.turn_order) == 4
                else "multiplayer commander_review"
            ),
            "seed": state.config.seed,
            "warnings": (
                [
                    "This is an explicit Commander duel/1v1 profile and must not be treated as four-player matchup evidence."
                ]
                if state.config.effective_profile(len(state.turn_order)) == "commander_duel"
                else []
            )
            + (
                [
                    "This protocol fixture duplicates deck lists and is classified as pilot_test, never matchup evidence."
                ]
                if duplicated_deck_fixture
                else []
            ),
        },
        "opening_hands": _opening_hands(engine),
        "players": {
            seat: {
                "deck": state.deck_names.get(seat, ""),
                "turns_begun": turns_begun[seat],
                "lands_played": land_counts[seat],
                "spells_cast": casts[seat],
                "draws_after_opening": draws[seat],
                "activated_abilities": sum(
                    event.code == "stack.activate" and event.actor == seat
                    for event in state.events
                ),
                "cleanup_discards": discards[seat],
                "attackers_declared": attacks[seat],
                "combat_damage_received": damage[seat],
                "commander_damage_received": dict(state.players[seat].commander_damage_received),
                "commander_damage_sources": [
                    {
                        "oracle_id": oracle_id,
                        "name": _oracle_name(engine, oracle_id),
                        "damage": amount,
                    }
                    for oracle_id, amount in state.players[seat].commander_damage_received.items()
                ],
                "commander_casts": commander_casts[seat],
                "land_drops_made": land_counts[seat],
                "unused_land_play_allowances_observed": max(
                    0, turns_begun[seat] - land_counts[seat]
                ),
                "unused_land_play_caveat": (
                    "This is state telemetry, not a pilot mistake. A missed land "
                    "drop is never attributed unless the exact payable land-play "
                    "opportunity was delivered and audited."
                ),
                "stranded_cards": stranded[seat],
                "ending_life": state.players[seat].life,
            }
            for seat in state.turn_order
        },
        "land_entry": land_review,
        "fetchlands": {
            "activations": sum(
                _is_typed_library_search_activation(engine, event, by_ref)
                for event in state.events
            ),
            "searches_resolved": counts["library.search"],
        },
        "development": {
            "first_three_player_turns": first_three,
            "mana_produced_by_turn": {
                str(turn): dict(values)
                for turn, values in sorted(mana_by_turn.items())
            },
            "mana_spent_by_turn": {
                str(turn): dict(values)
                for turn, values in sorted(mana_spent_by_turn.items())
            },
            "mana_left_unused_by_turn": {
                str(turn): dict(values)
                for turn, values in sorted(mana_unused_by_turn.items())
            },
            "mana_warning": (
                "Unused-mana events are unavailable in this compact or legacy trace."
                if not mana_unused_by_turn
                else None
            ),
        },
        "tutors_and_searches": tutors,
        "interaction_opportunities": {
            **opportunity_audit,
        },
        "pivotal_timeline": [
            {
                "turn": event.turn_sequence,
                "actor": event.actor,
                "code": event.code,
                "summary": event.summary,
            }
            for event in state.events
            if event.code in {
                "stack.cast",
                "stack.activate",
                "stack.counter",
                "library.search",
                "player.eliminated",
                "game.win",
                "game.draw",
            }
        ],
        "suspected_pilot_mistakes": suspected_pilot,
        "suspected_rules_or_semantics_failures": {
            "land_entry_conflicts": land_review["conflicts"],
            "unresolved_relevant_semantics": semantics["unresolved_relevant"],
            "decision_opportunity_infrastructure": [
                {
                    "turn": row.get("turn_sequence"),
                    "seat": row.get("seat"),
                    "phase": row.get("phase"),
                    "action_signature": row.get("action_signature"),
                    "finding": "Meaningful action window was not delivered.",
                }
                for row in opportunities
                if row.get("incorrectly_suppressed")
            ],
        },
        "win_route": {
            "winner": state.winner,
            "commander_damage": {
                seat: [
                    {
                        "oracle_id": oracle_id,
                        "name": _oracle_name(engine, oracle_id),
                        "damage": amount,
                    }
                    for oracle_id, amount in state.players[seat].commander_damage_received.items()
                ]
                for seat in state.turn_order
            },
            "description": (
                f"{state.winner} won after commander-damage state-based elimination."
                if state.winner
                and any(
                    sum(player.commander_damage_received.values())
                    >= state.config.commander_damage_to_lose
                    for player in state.players.values()
                )
                else (f"{state.winner} won." if state.winner else "Game incomplete.")
            ),
        },
        "semantic_coverage": semantics,
        "pilot_audit": {
            **copy.deepcopy(
                dict((manifest or {}).get("provider_telemetry") or {})
            ),
            "decision_records_observed": len(decisions),
            "pilot_invocations_observed": pilot_invocations,
            "arbiter_invocations_observed": arbiter_invocations,
            "automatic_decisions": automatic_decisions,
            "priority_windows_considered": optimization_totals[
                "priority_windows_considered"
            ],
            "pass_only_windows_skipped": optimization_totals[
                "pass_only_windows_skipped"
            ],
            "yield_covered_windows": optimization_totals[
                "yield_covered_windows"
            ],
            "suppressed_empty_windows": optimization_totals[
                "suppressed_empty_windows"
            ],
            "suppressed_meaningful_windows": suppressed_meaningful,
            "yields_invalidated_by_reason": {
                reason: optimization_totals[
                    f"yields_invalidated_by_{reason}"
                ]
                for reason in (
                    "phase",
                    "draw",
                    "action_change",
                    "stack",
                    "public_change",
                )
            },
            "action_opportunity_coverage": opportunity_coverage,
            "target_action_audit": {
                key: optimization_totals[key]
                for key in (
                    "illegal_target_actions_prevented",
                    "illegal_target_actions_advertised",
                    "actions_removed_for_no_targets",
                    "actions_removed_for_mode_target_failure",
                    "target_candidates_generated",
                    "target_submissions_rejected",
                    "targets_became_illegal_on_resolution",
                    "spells_countered_by_rules",
                    "spells_countered_by_effect",
                    "stack_interaction_windows_created",
                    "stack_interaction_windows_auto_passed",
                )
            },
            "profile_fingerprint_match": profile_match_value,
            "pilot_thread_count": arena.get("pilot_thread_count"),
            "persistent_thread_reuse": arena.get(
                "persistent_thread_reuse"
            ),
            "primary_made_strategic_decision": arena.get(
                "primary_made_strategic_decision", False
            ),
            "provider_identity_verified": arena.get(
                "provider_identity_verified"
            ),
            "model_identity_verified": arena.get(
                "model_identity_verified"
            ),
            "seat_projection_verified": arena.get(
                "seat_projection_verified"
            ),
            "codex_subagent_run": arena.get(
                "codex_subagent_run", False
            ),
            "ordered_plan_responses": int(
                arena.get("ordered_plan_responses", 0)
            ),
            "arena_stop_reason": arena.get("stop_reason"),
            "ordered_plan_actions_executed": automatic_decisions,
            "estimated_calls_without_optimization": len(decisions)
            + automatic_decisions
            + pass_decisions,
            "estimated_calls_with_optimization": (
                len(provider_rows) if not legacy_decisions else None
            ),
            "input_tokens_observed": (
                observed_input
                if token_status in {"complete", "partial"}
                else None
            ),
            "output_tokens_observed": (
                observed_output
                if token_status in {"complete", "partial"}
                else None
            ),
            "token_measurement_status": token_status,
            "pilot_input_tokens_observed": observed_role_tokens(
                pilot_provider_rows, "input_tokens"
            ),
            "pilot_output_tokens_observed": observed_role_tokens(
                pilot_provider_rows, "output_tokens"
            ),
            "arbiter_input_tokens_observed": observed_role_tokens(
                arbiter_provider_rows, "input_tokens"
            ),
            "arbiter_output_tokens_observed": observed_role_tokens(
                arbiter_provider_rows, "output_tokens"
            ),
            "attempts": len(decisions),
            "accepted": accepted,
            "rejected": rejected,
            "complete_alternatives": complete_alternatives,
            "complete_reasons": complete_reasons,
            "model_calls_observed": (
                len(provider_rows) if not legacy_decisions else None
            ),
            "legacy_priority_passes": pass_decisions,
            "potential_calls_avoided_by_empty-priority_auto-pass": pass_decisions,
            "before_after_model_call_estimate": {
                "before_observed": None if legacy_decisions else len(provider_rows),
                "after_if_every_observed_pass_were_proven_safe": max(
                    0, len(decisions) - pass_decisions
                ),
                "caveat": (
                    "This is an upper-bound estimate, not a claim that every historical pass was safely automatable."
                ),
            },
            "warning": (
                "Historical legal alternatives are unavailable; this review does not "
                "assert that a particular unplayed card was legal in a past state."
                if legacy_decisions or not decisions
                else None
            ),
        },
        "trace": {
            "authoritative_events_in_memory": len(state.events),
            "events_by_code": dict(sorted(counts.items())),
        },
        "turns": turn_groups,
        "fidelity": {
            "classification": classification,
            "review_eligible": eligible,
            "deck_operation_evidence": deck_operation_evidence,
            "deck_operation_failures": deck_operation_failures,
            "matchup_evidence": False,
            "failures": fidelity_failures,
            "dimensions": dimensions,
            "replay_verification": replay_status,
            "statement": (
                "This migrated or heuristic run is a smoke/protocol artifact, not evidence about deck quality or matchup balance."
                if classification == "smoke_only"
                else "This run is a rules/infrastructure test. It is not evidence about pilot quality, deck quality, or matchup balance."
                if classification == "rules_test"
                else "This native run is an unfinished pilot characterization; it is not evidence about deck quality or matchup balance."
                if classification == "pilot_test"
                else "This record is incomplete and is not deck-review evidence."
                if classification == "in_progress"
                else "This run passed the single-game deck-review fidelity gate; it is not sufficient matchup evidence."
            ),
        },
    }
    if record_directory:
        from .review_artifacts import record_size_comparison

        report["size_comparison"] = record_size_comparison(
            record_directory,
            manifest=manifest,
        )
    return report


def review_markdown(review: Mapping[str, Any]) -> str:
    fidelity = review["fidelity"]
    lines = [
        "# Commander game review",
        "",
        f"Game: {review['game_id']}",
        f"Outcome: {review['outcome']['winner'] or review['outcome']['status']}",
        f"Profile: {review['format']['profile']}",
        f"Fidelity: **{fidelity['classification']}** — review eligible: **{str(fidelity['review_eligible']).lower()}**",
        "",
        fidelity["statement"],
        "",
        "## Players",
        "",
    ]
    for seat, player in review["players"].items():
        spells = ", ".join(item["name"] for item in player["spells_cast"]) or "none"
        commander_damage = sum(player["commander_damage_received"].values())
        commander_sources = ", ".join(
            f"{item['damage']} from {item['name']}"
            for item in player["commander_damage_sources"]
        ) or "none"
        lines.append(
            f"- {seat} ({player['deck']}): {player['turns_begun']} turns, "
            f"{player['lands_played']} lands, spells {spells}, "
            f"{player['cleanup_discards']} cleanup discards, "
            f"{commander_damage} commander damage received ({commander_sources})."
        )
    lines.extend(
        [
            "",
            "## Audit findings",
            "",
            f"- Land plays: {review['land_entry']['plays']}; entry-state conflicts: {review['land_entry']['conflict_count']}.",
            f"- Fetchland activations: {review['fetchlands']['activations']}; resolved searches: {review['fetchlands']['searches_resolved']}.",
            f"- Pilot decision attempts: {review['pilot_audit']['attempts']}; accepted: {review['pilot_audit']['accepted']}; rejected: {review['pilot_audit']['rejected']}.",
            f"- Provider calls observed: pilots {review['pilot_audit']['pilot_invocations_observed']}; "
            f"arbiter {review['pilot_audit']['arbiter_invocations_observed']}; "
            f"token status {review['pilot_audit']['token_measurement_status']}.",
            f"- Semantic coverage: {review['semantic_coverage']['status']}.",
        ]
    )
    pause = review["outcome"].get("pause_reason")
    if review["outcome"].get("status") == "paused" and pause:
        lines.extend(
            [
                f"- Infrastructure status: paused for {pause.get('kind')} "
                f"at {pause.get('decision_id')} "
                f"({pause.get('label') or 'unlabeled decision'}).",
                "- Replay verification applies to the accepted-command prefix; "
                "it does not imply that the game ended.",
            ]
        )
    lines.extend(["", "### Fidelity dimensions", ""])
    lines.extend(
        f"- {name}: {value}"
        for name, value in fidelity["dimensions"].items()
    )
    if review.get("size_comparison"):
        size = review["size_comparison"]
        lines.extend(
            [
                "",
                "### Record size",
                "",
                f"- Legacy monolith: {size.get('legacy_game_json_bytes')} bytes.",
                f"- Current checkpoint: {size.get('checkpoint_bytes')} bytes.",
                f"- Command/event/decision journals: {size.get('command_journal_bytes')}/"
                f"{size.get('event_journal_bytes')}/{size.get('decision_journal_bytes')} bytes.",
                f"- Manifest: {size.get('manifest_bytes')} bytes; derived review: "
                f"{size.get('review_artifact_bytes')} bytes.",
                f"- Resumable core: {size.get('resumable_core_bytes')} bytes; "
                f"complete record: {size.get('complete_record_bytes')} bytes.",
                f"- Reduction before derived review: {size.get('percent_smaller_before_derived_review')}%.",
            ]
        )
    if review["land_entry"]["conflicts"]:
        lines.extend(["", "### Land-entry conflicts", ""])
        for conflict in review["land_entry"]["conflicts"]:
            lines.append(
                f"- Turn {conflict['turn']} {conflict['seat']} — {conflict['name']}: "
                f"recorded tapped={conflict['recorded_tapped']}, "
                f"expected={conflict['expected_tapped']} ({conflict['basis']})."
            )
    if fidelity["failures"]:
        lines.extend(["", "### Fidelity gate failures", ""])
        lines.extend(f"- {failure}" for failure in fidelity["failures"])
    lines.extend(["", "## Meaningful turn history", ""])
    for turn in review["turns"]:
        label = "Setup" if turn["turn"] == 0 else f"Turn {turn['turn']} ({turn['active_player']})"
        lines.append(f"### {label}")
        lines.append("")
        for event in turn["events"]:
            lines.append(f"- {event['summary']}")
        for opportunity in turn.get("action_opportunities", []):
            if (
                opportunity.get("seat") != turn.get("active_player")
                or opportunity.get("phase")
                not in {"precombat_main", "postcombat_main"}
            ):
                continue
            invalidated = opportunity.get("yield_invalidated_by")
            if invalidated:
                lines.append(
                    f"- Previous yield expired by {invalidated} before "
                    f"{opportunity['phase'].replace('_', ' ')}."
                )
            if opportunity.get("legal_land_plays"):
                lines.append(
                    "- Legal land plays: "
                    + ", ".join(opportunity["legal_land_plays"])
                    + "."
                )
            if opportunity.get("legal_casts"):
                lines.append(
                    "- Currently payable casts: "
                    + ", ".join(opportunity["legal_casts"])
                    + "."
                )
            if opportunity.get("chosen_action_id"):
                lines.append(
                    f"- Pilot chose {opportunity['chosen_action_id']}. "
                    f"Plan: {opportunity.get('plan') or 'UNSPECIFIED'}."
                )
            lines.append(
                "- No meaningful decision window was suppressed."
                if not opportunity.get("incorrectly_suppressed")
                else "- Infrastructure failure: this meaningful decision window was suppressed."
            )
        for decision in turn.get("decisions", []):
            if decision.get("legacy_incomplete"):
                lines.append(
                    "- Decision audit: migrated v2 entry; no historical reason "
                    "or complete legal-action catalog is available."
                )
                continue
            plan = decision.get("plan") or "UNSPECIFIED"
            reason = decision.get("reason") or "No reason recorded."
            lines.append(f"- Decision — Plan: {plan}. Reason: {reason}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_review_artifacts(
    directory: str | Path,
    engine: CommanderEngine,
    *,
    decisions: Sequence[Mapping[str, Any]] = (),
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility facade for the derived review-artifact owner."""

    from .review_artifacts import write_review_artifacts as write_artifacts

    return write_artifacts(
        directory,
        engine,
        decisions=decisions,
        manifest=manifest,
    )


def concise_report(engine: CommanderEngine) -> str:
    return review_markdown(derive_review(engine))


def load_review(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if path.is_dir():
        path = path / "review.json"
    return json.loads(path.read_text(encoding="utf-8"))
