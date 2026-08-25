from __future__ import annotations

import copy
from contextlib import AbstractContextManager
from typing import Any, Mapping, Protocol

from .errors import GameRuleError
from .mana_undo import clear_mana_undo_stack
from .rules.activation_zone_change_costs import (
    activation_zone_change_cost_reference,
)
from .rules.morph_actions import commit_turn_face_up
from .replacement.ordering import (
    ReplacementChoiceRequired,
    replacement_choice_payload,
)
from .replacement.model import ReplacementEffectError


_PILOT_ROLE = "pilot"


class ManaPaymentContinuationHost(Protocol):
    state: Any
    permissions: Any

    def transaction(self) -> AbstractContextManager[None]: ...

    def _cast(self, seat: str, response: Mapping[str, Any]) -> None: ...

    def _activate(self, seat: str, response: Mapping[str, Any]) -> None: ...


def issue_mana_payment_replacement_choice(
    host: ManaPaymentContinuationHost,
    *,
    seat: str,
    action: str,
    response: Mapping[str, Any],
    required: ReplacementChoiceRequired,
) -> None:
    """Suspend a rolled-back cast/activation cost at a CR 616 choice."""

    if action not in {"cast", "activate", "turn_face_up"}:
        raise ReplacementEffectError(
            "Only represented priority actions have resumable mana payments"
        )
    pending = required.pending
    if pending.choice.chooser != seat:
        raise ReplacementEffectError(
            "Priority-action cost replacement must be chosen by the affected player"
        )
    event_kinds = tuple(event.kind for event in required.batch.events)
    if event_kinds and all(kind == "damage" for kind in event_kinds):
        resume_kind = "mana_payment"
    elif event_kinds == ("counter.place",) and action in {"cast", "activate"}:
        resume_kind = "priority_action_cost"
    elif event_kinds == ("zone.change",) and action == "cast":
        resume_kind = "priority_action_cost"
    elif event_kinds == ("zone.change",) and action == "activate":
        event = required.batch.events[0]
        origin = event.payload.get("origin")
        destination = event.payload.get("destination")
        object_ref = event.payload.get("object_ref")
        if activation_zone_change_cost_reference(
            response,
            origin=origin,
            destination=destination,
            object_ref=object_ref,
        ) is None:
            raise ReplacementEffectError(
                "Activation zone-change cost replacement is unsupported"
            )
        resume_kind = "priority_action_cost"
    else:
        raise ReplacementEffectError(
            "Priority-action cost replacement event is unsupported"
        )
    context = replacement_choice_payload(pending, required.effects)
    host.permissions.issue(
        kind="replacement.order",
        role=_PILOT_ROLE,
        actors=[seat],
        allowed_actions=["choose"],
        payload_by_actor={seat: context},
        continuation={
            "replacement_resume_kind": resume_kind,
            "priority_seat": seat,
            "priority_action": action,
            "priority_response": copy.deepcopy(dict(response)),
            "priority_frame": {
                "active_player": host.state.active_player,
                "phase": host.state.phase,
                "step": host.state.step,
                "turn_sequence": host.state.turn_sequence,
                "priority_player": host.state.priority_player,
                "priority_epoch": host.state.priority_epoch,
                "stack_refs": [item.ref for item in host.state.stack],
            },
            "replacement_batch": required.batch.to_dict(),
            "replacement_effects": [
                replacement.to_dict() for replacement in required.effects
            ],
        },
    )


def execute_mana_choice_capable_priority_action(
    host: ManaPaymentContinuationHost,
    *,
    seat: str,
    action: str,
    response: Mapping[str, Any],
    payment_id: str,
    trusted_resume: bool = False,
) -> bool:
    """Run one payment atomically or replace it with a strict continuation."""

    if action not in {"cast", "activate", "turn_face_up"}:
        raise ValueError(
            "Only represented priority actions may suspend mana payment"
        )
    internal_fields = {
        "_mana_payment_id",
        "_mana_replacement_selections",
    }
    if not trusted_resume and internal_fields.intersection(response):
        raise GameRuleError(
            "Internal payment-continuation fields cannot be submitted"
        )
    payload = dict(response)
    payload.setdefault("_mana_payment_id", str(payment_id))
    try:
        # A replacement choice is discovered while resolving a mana ability.
        # The nested boundary restores every tap, mana, life, and cost change
        # while leaving the already-authorized priority decision closed.
        with host.transaction():
            if action == "cast":
                clear_mana_undo_stack(host.state.players[seat].stats)
                host._cast(seat, payload)
            elif action == "activate":
                host._activate(seat, payload)
            else:
                commit_turn_face_up(
                    host,
                    seat=seat,
                    response=payload,
                )
    except ReplacementChoiceRequired as required:
        issue_mana_payment_replacement_choice(
            host,
            seat=seat,
            action=action,
            response=payload,
            required=required,
        )
        return False
    return True


def resume_mana_choice_capable_priority_action(
    host: ManaPaymentContinuationHost,
    *,
    seat: str,
    action: str,
    response: Mapping[str, Any],
) -> None:
    """Revalidate and resume the exact rolled-back cast or activation."""

    payment_id = str(response.get("_mana_payment_id") or "")
    if not payment_id:
        raise GameRuleError(
            "Mana-payment continuation lost its stable identity"
        )
    resumed_response = dict(response)
    # The original offer's revision necessarily expires while the replacement
    # choice is issued. The pinned frame protects the intervening state; the
    # proposal builder below revalidates the exact action from current facts.
    resumed_response.pop("proposal_fingerprint", None)
    resumed_response.pop("expiry_revision", None)
    execute_mana_choice_capable_priority_action(
        host,
        seat=seat,
        action=action,
        response=resumed_response,
        payment_id=payment_id,
        trusted_resume=True,
    )


__all__ = [
    "execute_mana_choice_capable_priority_action",
    "issue_mana_payment_replacement_choice",
    "resume_mana_choice_capable_priority_action",
]
