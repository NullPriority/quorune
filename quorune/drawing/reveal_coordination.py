from __future__ import annotations

"""Authenticated battlefield draw-reveal choices and exact continuation checks.

The draw coordinator retains the single instruction trampoline, and the draw
transaction retains all card movement, reveal, and history mutation.
"""

from dataclasses import replace
from typing import Any, TYPE_CHECKING

from ..replacement import FrozenMap
from ..replacement.immutable import thaw_value
from ..semantic_runtime.context import SemanticNodeError
from ..semantic_runtime.draw_reveals import DrawRevealPolicy, collect_draw_reveal_policies
from .continuation import DrawResume, DrawRevealDecisionContinuation
from .model import DrawError, PreparedDrawEvent, RevealDrawnCardBySource, prepare_draw_event
from .transaction import commit_prepared_draw_result
from .miracle_coordination import prepare_miracle_or_issue_choice

if TYPE_CHECKING:
    from .coordinator import DrawCoordinatorHost

_LIBRARY_ZONE = "library"
_PILOT_ROLE = "pilot"


def _draw_reveal_policies(
    host: DrawCoordinatorHost,
    seat: str,
) -> tuple[DrawRevealPolicy, ...]:
    try:
        return collect_draw_reveal_policies(host, seat)
    except SemanticNodeError as exc:
        raise DrawError(str(exc)) from exc


def _reveal_action(policy: DrawRevealPolicy) -> RevealDrawnCardBySource:
    return RevealDrawnCardBySource(
        source_object_id=policy.source_object_id,
        source_ref=policy.source_ref,
        source_logical_object_id=policy.source_logical_object_id,
        source_zone_change_counter=policy.source_zone_change_counter,
    )


def _prepared_with_reveal_policies(
    host: DrawCoordinatorHost,
    prepared: PreparedDrawEvent,
    policies: tuple[DrawRevealPolicy, ...],
) -> PreparedDrawEvent:
    if not policies:
        return prepared
    request = replace(
        prepared.request,
        post_draw_actions=(
            *(_reveal_action(policy) for policy in policies),
            *prepared.request.post_draw_actions,
        ),
    )
    rebuilt = prepare_draw_event(
        request,
        apnap_order=host.apnap_order(),
        effects=prepared.effects,
        selections=tuple(
            selection.to_dict() for selection in prepared.journal
        ),
    )
    if rebuilt.pending is not None or rebuilt.resolution is None:
        raise DrawError(
            "Source-linked reveal changed a closed draw replacement result"
        )
    return rebuilt


def _issue_draw_reveal_choice(
    host: DrawCoordinatorHost,
    continuation: DrawRevealDecisionContinuation,
) -> None:
    index = continuation.optional_policy_index
    policy = DrawRevealPolicy.from_dict(
        thaw_value(continuation.optional_policies[index])
    )
    card = host.state.cards.get(continuation.drawn_object_id)
    source = host.state.cards.get(policy.source_object_id)
    if (
        card is None
        or card.zone != _LIBRARY_ZONE
        or not host.state.players[continuation.seat].zones[_LIBRARY_ZONE]
        or host.state.players[continuation.seat].zones[_LIBRARY_ZONE][-1]
        != card.object_id
    ):
        raise DrawError("The card awaiting a reveal choice changed")
    if (
        source is None
        or source.ref != policy.source_ref
        or source.logical_object_id != policy.source_logical_object_id
        or source.zone_change_counter != policy.source_zone_change_counter
        or source.zone != "battlefield"
        or source.phased_out
    ):
        raise DrawError("The optional draw reveal source changed")
    record = host.card_record(card)
    if record is None:
        raise DrawError(
            "An optional draw reveal requires pinned card characteristics"
        )
    host.permissions.issue(
        kind="draw.reveal",
        role=_PILOT_ROLE,
        actors=[continuation.seat],
        allowed_actions=["reveal", "decline"],
        payload_by_actor={
            continuation.seat: {
                "card": {
                    "id": card.ref,
                    "name": card.printed_name,
                    "type_line": record.type_line,
                },
                "source": {
                    "id": source.ref,
                    "name": source.printed_name,
                    "policy_id": policy.policy_id,
                },
                "question": "Reveal this card as you draw it?",
                "legal_actions": [
                    {"id": "reveal", "action": "reveal"},
                    {"id": "decline", "action": "decline"},
                ],
            }
        },
        continuation=continuation.to_dict(),
    )


def _prepare_reveal_or_issue_choice(
    host: DrawCoordinatorHost,
    prepared: PreparedDrawEvent,
    *,
    remaining_draws: int,
    resume: DrawResume,
) -> PreparedDrawEvent | None:
    resolution = prepared.resolution
    if resolution is None or resolution.kind != "draw":
        return prepared
    library = host.state.players[resolution.player].zones[_LIBRARY_ZONE]
    if not library:
        return prepared
    policies = _draw_reveal_policies(host, resolution.player)
    if not policies:
        return prepare_miracle_or_issue_choice(
            host, prepared, remaining_draws=remaining_draws, resume=resume,
            instruction_post_draw_actions=prepared.request.post_draw_actions,
        )
    mandatory = tuple(policy for policy in policies if not policy.optional)
    optional = tuple(policy for policy in policies if policy.optional)
    if not optional:
        return prepare_miracle_or_issue_choice(
            host, _prepared_with_reveal_policies(host, prepared, mandatory),
            remaining_draws=remaining_draws, resume=resume, source_policies=policies,
            instruction_post_draw_actions=prepared.request.post_draw_actions,
        )
    continuation = DrawRevealDecisionContinuation(
        event_id=prepared.request.event_id,
        seat=resolution.player,
        remaining_draws=remaining_draws,
        library_size=prepared.request.library_size,
        drawn_object_id=library[-1],
        reason=resolution.reason,
        private=resolution.private,
        effects=prepared.effects,
        journal=prepared.journal,
        after=resume,
        mandatory_policies=tuple(
            FrozenMap(policy.to_dict()) for policy in mandatory
        ),
        optional_policies=tuple(
            FrozenMap(policy.to_dict()) for policy in optional
        ),
        excluded_effect_ids=prepared.request.excluded_effect_ids,
        post_draw_actions=prepared.request.post_draw_actions,
    )
    _issue_draw_reveal_choice(host, continuation)
    return None


def complete_draw_reveal(
    host: DrawCoordinatorHost,
    decision: Any,
) -> None:
    from .coordinator import _replacement_effects, _continue_draw_sequence
    continuation = DrawRevealDecisionContinuation.from_dict(
        decision.continuation
    )
    seat = decision.actors[0]
    if seat != continuation.seat:
        raise DrawError("Draw reveal continuation seat changed")
    response = decision.responses[seat]
    action = response.get("action")
    if action not in {"reveal", "decline"}:
        raise DrawError("Draw reveal choice must be reveal or decline")

    current_effects = _replacement_effects(
        host,
        seat,
        continuation.excluded_effect_ids,
    )
    if current_effects != continuation.effects:
        raise DrawError(
            "Draw replacement sources changed before reveal completion"
        )
    current_policies = _draw_reveal_policies(host, seat)
    mandatory = tuple(
        policy for policy in current_policies if not policy.optional
    )
    optional = tuple(
        policy for policy in current_policies if policy.optional
    )
    if tuple(policy.to_dict() for policy in mandatory) != tuple(
        thaw_value(value) for value in continuation.mandatory_policies
    ) or tuple(policy.to_dict() for policy in optional) != tuple(
        thaw_value(value) for value in continuation.optional_policies
    ):
        raise DrawError("Draw reveal policies changed before completion")

    library = host.state.players[seat].zones[_LIBRARY_ZONE]
    if (
        len(library) != continuation.library_size
        or not library
        or library[-1] != continuation.drawn_object_id
    ):
        raise DrawError("The card awaiting a reveal choice changed")
    prepared = prepare_draw_event(
        continuation.request,
        apnap_order=host.apnap_order(),
        effects=continuation.effects,
        selections=tuple(
            selection.to_dict() for selection in continuation.journal
        ),
    )
    if (
        prepared.pending is not None
        or prepared.resolution is None
        or prepared.resolution.kind != "draw"
    ):
        raise DrawError("Draw reveal continuation no longer resolves a draw")

    current_policy = optional[continuation.optional_policy_index]
    selected = continuation.selected_policy_ids
    if action == "reveal":
        selected = (*selected, current_policy.policy_id)
    next_index = continuation.optional_policy_index + 1
    if next_index < len(optional):
        _issue_draw_reveal_choice(
            host,
            replace(
                continuation,
                optional_policy_index=next_index,
                selected_policy_ids=selected,
            ),
        )
        return

    selected_set = set(selected)
    reveal_policies = (
        *mandatory,
        *(
            policy
            for policy in optional
            if policy.policy_id in selected_set
        ),
    )
    prepared = _prepared_with_reveal_policies(
        host,
        prepared,
        tuple(reveal_policies),
    )
    prepared = prepare_miracle_or_issue_choice(
        host, prepared, remaining_draws=continuation.remaining_draws,
        resume=continuation.after, source_policies=current_policies,
        instruction_post_draw_actions=continuation.post_draw_actions,
    )
    if prepared is None:
        return
    commit_prepared_draw_result(host, prepared)
    _continue_draw_sequence(
        host,
        seat,
        continuation.remaining_draws - 1,
        reason=continuation.reason,
        private=continuation.private,
        resume=continuation.after,
        excluded_effect_ids=continuation.excluded_effect_ids,
        post_draw_actions=continuation.post_draw_actions,
    )
