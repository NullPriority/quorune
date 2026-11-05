from __future__ import annotations

"""An intrinsic reveal decision suspended inside the existing draw trampoline."""

from typing import Any

from ..semantic_runtime.context import SemanticNodeError
from .continuation import DrawResume
from .miracle_continuation import MiracleRevealContinuation
from .miracle_discovery import discover_miracle_draw
from .miracle_model import MIRACLE_REVEAL_EVENT, MiracleModelError
from .model import DrawError, DrawnCardAction, PreparedDrawEvent
from .transaction import commit_prepared_draw_result


def prepare_miracle_or_issue_choice(
    host: Any, prepared: PreparedDrawEvent, *, remaining_draws: int,
    resume: DrawResume, source_policies=(), instruction_post_draw_actions: tuple[DrawnCardAction, ...],
) -> PreparedDrawEvent | None:
    result = prepared.resolution
    if result is None or result.kind != "draw":
        return prepared
    try:
        opportunity = discover_miracle_draw(host, player=result.player, event_id=prepared.request.event_id)
    except (MiracleModelError, SemanticNodeError) as exc:
        raise DrawError(str(exc)) from exc
    if opportunity is None:
        return prepared
    continuation = MiracleRevealContinuation(
        opportunity=opportunity, request=prepared.request, effects=prepared.effects,
        journal=prepared.journal, remaining_draws=remaining_draws, after=resume,
        instruction_post_draw_actions=instruction_post_draw_actions, source_policies=source_policies,
    )
    card = host.state.cards[opportunity.card_object_id]
    record = host.card_record(card)
    if record is None:
        raise DrawError("Miracle reveal requires pinned card characteristics")
    host.permissions.issue(
        kind=MIRACLE_REVEAL_EVENT, role="pilot", actors=[opportunity.drawer],
        allowed_actions=["reveal", "decline"],
        payload_by_actor={opportunity.drawer: {
            "card": {"id": card.ref, "name": card.printed_name, "type_line": record.type_line},
            "question": f"Reveal this first drawn card to trigger Miracle {opportunity.cost.cost_text}?",
            "miracle_cost": opportunity.cost.cost_text,
            "legal_actions": [{"id": "reveal", "action": "reveal"},
                              {"id": "decline", "action": "decline"}],
        }},
        continuation=continuation.to_dict(),
    )
    return None


def complete_miracle_reveal(host: Any, decision: Any) -> None:
    from .coordinator import _replacement_effects, _continue_draw_sequence
    from .reveal_coordination import _draw_reveal_policies

    try:
        continuation = MiracleRevealContinuation.from_dict(decision.continuation)
    except ValueError as exc:
        raise DrawError(str(exc)) from exc
    opportunity = continuation.opportunity
    seat = opportunity.drawer
    if tuple(decision.actors) != (seat,) or set(decision.responses) != {seat}:
        raise DrawError("Miracle reveal requires its original drawer")
    response = decision.responses[seat]
    if set(response) != {"action"} or response["action"] not in {"reveal", "decline"}:
        raise DrawError("Miracle reveal choice must be reveal or decline")
    if _replacement_effects(host, seat, continuation.request.excluded_effect_ids) != continuation.effects:
        raise DrawError("Miracle draw replacement sources changed")
    if _draw_reveal_policies(host, seat) != continuation.source_policies:
        raise DrawError("Miracle battlefield reveal policies changed")
    try:
        current = discover_miracle_draw(host, player=seat, event_id=opportunity.event_id)
    except (MiracleModelError, SemanticNodeError) as exc:
        raise DrawError(str(exc)) from exc
    if current != opportunity:
        raise DrawError("Miracle prospective draw or current source program changed")
    prepared = continuation.prepare(apnap_order=host.apnap_order())
    commit_prepared_draw_result(
        host, prepared, miracle=opportunity if response["action"] == "reveal" else None,
    )
    _continue_draw_sequence(
        host, seat, continuation.remaining_draws - 1, reason=continuation.request.reason,
        private=continuation.request.private, resume=continuation.after,
        excluded_effect_ids=continuation.request.excluded_effect_ids,
        post_draw_actions=continuation.instruction_post_draw_actions,
    )
