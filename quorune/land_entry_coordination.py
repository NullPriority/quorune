from __future__ import annotations

"""Atomic priority-action coordination for CR 614/616 land entry."""

import copy
from contextlib import AbstractContextManager
from typing import Any, Mapping, Protocol, Sequence

from .errors import GameRuleError
from .model import LAND_ENTRY_PAYMENT_VALIDATION_FIELD, land_entry_affordability_version
from .life_state import LifeStateError, prepare_life_payment
from .mana_undo import clear_mana_undo_stack
from .replacement.operations import AddAmount
from .replacement.ordering import (
    ReplacementChoiceRequired,
    replacement_choice_payload,
)
from .replacement.model import ReplacementEffectError


_PILOT_ROLE = "pilot"
SKIP_UNAVAILABLE_LAND_ENTRY_PAYMENT = "skip-unavailable-land-entry-payment"
_INTERNAL_FIELDS = {
    "_entry_action_id",
    "_entry_replacement_selections",
}


class LandEntryContinuationHost(Protocol):
    state: Any
    permissions: Any

    def transaction(self) -> AbstractContextManager[None]: ...

    def _play_land(self, seat: str, response: Mapping[str, Any]) -> None: ...


class LandEntryLifeUnavailable(GameRuleError):
    """The original priority action cannot pay the selected entry price."""


def validate_explicit_land_entry_payment(host: LandEntryContinuationHost, seat: str, amount: int) -> None:
    if amount <= 0:
        raise GameRuleError("This land play does not authorize an entry life payment")
    version = land_entry_affordability_version(
        getattr(host.state, LAND_ENTRY_PAYMENT_VALIDATION_FIELD, None),
    )
    if version == 1:
        try:
            prepare_life_payment(host, seat, amount)
        except LifeStateError as exc:
            raise LandEntryLifeUnavailable(str(exc)) from exc


def can_skip_unavailable_land_entry_payment(state, restored, seat):
    if restored.resume_kind != "land_entry" or restored.priority_seat != seat or seat not in state.players:
        return False
    response = restored.thaw_priority_response()
    card_ref = response.get("card") or response.get("id")
    if not response.get("pay_life") or type(card_ref) is not str or len(restored.batch.events) != 1:
        return False
    event, = restored.batch.events
    if (event.kind != "zone.change" or event.payload.get("object_ref") != card_ref
            or event.payload.get("destination") != "battlefield"
            or event.payload.get("destination_controller") != seat):
        return False
    price = sum(operation.amount for effect in restored.effects if effect.source_id == card_ref
                for operation in effect.operations
                if isinstance(operation, AddAmount) and operation.field == "entry_life_payment")
    return price > 0 and state.players[seat].life < price


def offer_land_entry_payment_recovery(state, decision, seat, context):
    if (decision.kind != "replacement.order"
            or decision.continuation.get("replacement_resume_kind") != "land_entry"):
        return
    from .replacement.replay import ReplacementContinuation

    try:
        restored = ReplacementContinuation.from_dict(decision.continuation)
        available = can_skip_unavailable_land_entry_payment(state, restored, seat)
    except ReplacementEffectError:
        return
    if available:
        context["options"] = [
            *context.get("options", ()),
            {"id": SKIP_UNAVAILABLE_LAND_ENTRY_PAYMENT, "label": "Skip life payment", "source": ""},
        ]


def _priority_frame(host: LandEntryContinuationHost) -> dict[str, Any]:
    return {
        "active_player": host.state.active_player,
        "phase": host.state.phase,
        "step": host.state.step,
        "turn_sequence": host.state.turn_sequence,
        "priority_player": host.state.priority_player,
        "priority_epoch": host.state.priority_epoch,
        "stack_refs": [item.ref for item in host.state.stack],
    }


def issue_land_entry_replacement_choice(
    host: LandEntryContinuationHost,
    *,
    seat: str,
    response: Mapping[str, Any],
    required: ReplacementChoiceRequired,
) -> None:
    """Issue one private CR 616 choice after rolling back the land play."""

    pending = required.pending
    if pending.choice.chooser != seat:
        raise ReplacementEffectError(
            "Land-entry replacement must be chosen by the affected player"
        )
    if not required.batch.events or any(
        event.kind != "zone.change" for event in required.batch.events
    ):
        raise ReplacementEffectError(
            "Land-entry continuation requires zone-change replacement events"
        )
    raw_selections = response.get("_entry_replacement_selections", ())
    if not isinstance(raw_selections, (list, tuple)):
        raise ReplacementEffectError(
            "Land-entry replacement journal is malformed"
        )
    context = replacement_choice_payload(pending, required.effects)
    host.permissions.issue(
        kind="replacement.order",
        role=_PILOT_ROLE,
        actors=[seat],
        allowed_actions=["choose"],
        payload_by_actor={seat: context},
        continuation={
            "replacement_resume_kind": "land_entry",
            "priority_seat": seat,
            "priority_action": "play_land",
            "priority_response": copy.deepcopy(dict(response)),
            "priority_frame": _priority_frame(host),
            "replacement_selections": copy.deepcopy(list(raw_selections)),
            "replacement_batch": required.batch.to_dict(),
            "replacement_effects": [
                replacement.to_dict() for replacement in required.effects
            ],
        },
    )


def execute_land_entry_priority_action(
    host: LandEntryContinuationHost,
    *,
    seat: str,
    response: Mapping[str, Any],
    entry_action_id: str,
    trusted_resume: bool = False,
) -> bool:
    """Run a land play atomically or replace it with a strict continuation."""

    if not trusted_resume and _INTERNAL_FIELDS.intersection(response):
        raise GameRuleError(
            "Internal land-entry continuation fields cannot be submitted"
        )
    if type(entry_action_id) is not str or not entry_action_id:
        raise GameRuleError("Land entry requires a stable action identity")
    payload = dict(response)
    payload.setdefault("_entry_action_id", entry_action_id)
    payload.setdefault("_entry_replacement_selections", [])
    try:
        with host.transaction():
            clear_mana_undo_stack(host.state.players[seat].stats)
            host._play_land(seat, payload)
    except ReplacementChoiceRequired as required:
        issue_land_entry_replacement_choice(
            host,
            seat=seat,
            response=payload,
            required=required,
        )
        return False
    return True


def resume_land_entry_priority_action(
    host: LandEntryContinuationHost,
    *,
    seat: str,
    response: Mapping[str, Any],
    selections: Sequence[str | Mapping[str, Any]],
) -> None:
    """Revalidate and replay the exact rolled-back land entry."""

    entry_action_id = str(response.get("_entry_action_id") or "")
    if not entry_action_id:
        raise GameRuleError(
            "Land-entry continuation lost its stable identity"
        )
    resumed = dict(response)
    resumed["_entry_replacement_selections"] = [
        copy.deepcopy(value) for value in selections
    ]
    resumed.pop("proposal_fingerprint", None)
    resumed.pop("expiry_revision", None)
    execute_land_entry_priority_action(
        host,
        seat=seat,
        response=resumed,
        entry_action_id=entry_action_id,
        trusted_resume=True,
    )


__all__ = [
    "execute_land_entry_priority_action",
    "issue_land_entry_replacement_choice",
    "resume_land_entry_priority_action",
]
