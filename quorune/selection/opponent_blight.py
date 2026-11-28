from __future__ import annotations

"""Bind a closed Blight result to the existing APNAP object-choice transaction."""

from ..errors import GameRuleError
from ..rules.opponent_blight import (
    OPPONENT_BLIGHT_BATCH_KIND,
    OpponentBlightBatchIntent,
    OpponentBlightSelection,
    uses_opponent_blight,
    validate_opponent_blight_effect,
)
from ..semantic_choices.apnap_commit import APNAP_OBJECT_COMMIT_OPERATION
from ..semantic_choices.blight import blight_creature_can_receive

OPPONENT_BLIGHT_INCARNATIONS = "opponent_blight_incarnations"


def validate_opponent_blight_choice(host, effect, continuation):
    item = next((row for row in host.state.stack if row.ref == continuation.get("stack_ref")), None)
    key = getattr(item, "semantic_key", None)
    registry = getattr(host, "semantics", None)
    program = registry.get(key) if registry is not None and key else None
    is_blight_program = program is not None and any(uses_opponent_blight(effect) for effect in program.effects)
    if not (uses_opponent_blight(effect) or is_blight_program):
        return None
    if (
        not is_blight_program or item.kind != "triggered_ability" or item.targets
        or item.controller not in host.active_seats
        or item.context.get("currently_resolving") is not True
        or item.context.get("event") != "permanent.enter"
        or not host.semantic_program_is_current_trusted(program)
        or len(program.effects) != 1
    ):
        raise GameRuleError("Opposing-player Blight requires its trusted resolving entry trigger")
    try:
        validate_opponent_blight_effect(program.effects[0])
        validate_opponent_blight_effect(effect, item.controller)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    return item


def opponent_blight_candidates(host, seat):
    query = host._semantic_choice_query(seat)
    return {
        row.ref: row.logical_object_id
        for row in sorted(query.objects(zones=("battlefield",), controller=seat), key=lambda entry: entry.ref)
        if blight_creature_can_receive(row, seat, 1, query)
    }


def apply_opponent_blight_choices(host, state, item):
    resume = state["resume"]
    order = [seat for seat in host.apnap_order() if seat != item.controller]
    selected = state["selected"]
    incarnations = state.get(OPPONENT_BLIGHT_INCARNATIONS, {})
    if order != list(state["player_order"]) or set(selected) != set(order) or state["queue"]:
        raise GameRuleError("Opposing-player Blight choice order changed")
    rows = []
    for seat in order:
        candidates = opponent_blight_candidates(host, seat)
        refs = selected[seat]
        if not isinstance(refs, (list, tuple)) or len(refs) != min(1, len(candidates)):
            raise GameRuleError("A placing opponent must choose one eligible creature")
        if refs:
            ref = refs[0]
            identity = incarnations.get(ref)
            if identity is None or candidates.get(ref) != identity:
                raise GameRuleError("An opposing-player Blight selection changed incarnation")
            rows.append(OpponentBlightSelection(seat, ref, identity))
    if set(incarnations) != {row.ref for row in rows}:
        raise GameRuleError("Opposing-player Blight contains an unselected incarnation")
    remaining = list(resume.get("effects", ()))
    if rows:
        try:
            intent = OpponentBlightBatchIntent(item.controller, host._stack_source_ref(item), tuple(rows))
        except (TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc
        remaining.insert(0, {"op": APNAP_OBJECT_COMMIT_OPERATION, OPPONENT_BLIGHT_BATCH_KIND: intent.identity()})
    host._continue_resolution(
        stack_ref=item.ref,
        effects=remaining,
        destination=resume.get("destination"),
        note=str(resume.get("note") or ""),
        instruction_pointer=int((resume.get("semantic_frame") or {}).get("instruction_pointer", 0)),
    )
