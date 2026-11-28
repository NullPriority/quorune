from __future__ import annotations

from typing import Any

from ..card_program_faces import program_matches_face
from ..card_programs.admission import program_has_complete_card_program_admission
from ..errors import GameRuleError
from ..semantic_runtime.action_permissions import (
    ACTION_PERMISSION_EVENT,
    MULLIGAN_REDRAW_HANDLER_ID,
    ActionPermissionSourceContext,
    default_action_permission_registry,
)
from ..zone_transitions import ZoneTransitionOwner


MULLIGAN_REDRAW_ACTION = "exile_hand_and_redraw"
MULLIGAN_REDRAW_OFFER = "mulligan_redraw_offer"
_REDRAW_REASON = "mulligan permission"


def hand_redraw_offer(host, seat: str) -> dict[str, str] | None:
    """Find a complete, current hand permission before the seat declares."""

    state = host.state
    player = state.players[seat]
    free = state.config.effective_free_mulligans(len(state.turn_order))
    if (
        state.started or state.phase != "setup" or state.step != "mulligan"
        or not player.in_game or player.kept_hand or player.mulligan_status != "pending"
        or player.mulligans_taken - free >= state.config.opening_hand_size
    ):
        return None

    for object_id in player.zones["hand"]:
        source = state.cards[object_id]
        if source.zone != "hand" or source.owner != seat or source.face_down or source.is_token:
            continue
        programs = host.semantics.runtime_handler_programs_for_oracle(
            source.oracle_id, active_zone="hand", event=ACTION_PERMISSION_EVENT,
        )
        for program in programs:
            descriptor = next((item for item in program.handlers
                if item.get("handler_id") == MULLIGAN_REDRAW_HANDLER_ID), None)
            if (
                descriptor is None or not host.semantic_program_is_current_trusted(program)
                or not program_has_complete_card_program_admission(program)
            ):
                continue
            record = host.card_record(source)
            if record is None or not program_matches_face(record, program, source):
                continue
            context = ActionPermissionSourceContext(
                source.ref, source.logical_object_id, program.key, seat, tuple(host.active_seats),
            )
            permission, = default_action_permission_registry().lower(descriptor, context)
            return {
                "source_ref": permission.source_ref,
                "source_logical_object_id": permission.source_logical_object_id,
                "semantic_key": permission.source_semantic_key,
            }
    return None


def commit_hand_redraw(host, seat: str, offered: Any) -> None:
    """Exile the current hand publicly, then draw the exiled count privately."""

    with host.transaction():
        current = hand_redraw_offer(host, seat)
        if current is None or current != offered:
            raise GameRuleError("The mulligan redraw permission is no longer available")
        hand = tuple(host.state.players[seat].zones["hand"])
        moved = ZoneTransitionOwner(host).move_cards_simultaneously(
            tuple((object_id, "exile") for object_id in hand), reason=_REDRAW_REASON, log=True,
        )
        exiled_count = sum(card.zone == "exile" for card in moved)
        host.draw(seat, exiled_count, reason=_REDRAW_REASON, private=True)
        host._log(
            seat, "mulligan.exile_and_redraw",
            f"{seat} exiled {exiled_count} card(s) and redrew their hand.",
            {"exiled_count": exiled_count}, changed_players=[seat],
        )
