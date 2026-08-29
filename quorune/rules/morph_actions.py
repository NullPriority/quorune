from __future__ import annotations

"""Offer and commit the represented CR 702.37e Morph special action."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Protocol

from ..compiled_morph import compiled_fixed_mana_morph_spec
from ..errors import GameRuleError
from ..mana_undo import clear_mana_undo_stack
from ..morph import (
    current_face_up_has_morph,
    MORPH_CAST_METHOD,
    MorphError,
    validate_morph_face_down_state,
)
from ..zone_object_state import ZoneObjectStateError, turn_card_face_up
from .action_proposals import ActionOffer, freeze_json


class MorphActionHost(Protocol):
    state: Any
    seats: Sequence[str]
    turn_priority: Any

    def _check_priority(self, seat: str) -> None: ...

    def _resolve_object(
        self,
        actor: str,
        ref: str,
        *,
        zones: set[str],
        controlled_only: bool = False,
    ) -> Any: ...

    def _cost_is_affordable(
        self,
        seat: str,
        requirements: Mapping[str, int],
        *,
        spend_context: Any = None,
    ) -> bool: ...

    def _pay_for_cost(
        self,
        seat: str,
        requirements: Mapping[str, int],
        response: Mapping[str, Any],
        *,
        spend_context: Any = None,
    ) -> tuple[dict[str, int], list[dict[str, Any]]]: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        summary: str,
        details: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any: ...

    def _dispatch_semantic_event(
        self,
        event: str,
        context: Mapping[str, Any],
        **kwargs: Any,
    ) -> list[str]: ...

    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> Sequence[Any]: ...

def _eligible_spec(host: MorphActionHost, seat: str, card: Any) -> Any | None:
    if (
        card.zone != "battlefield"
        or card.controller != seat
        or card.phased_out
        or card.object_kind != "card"
        or not card.face_down
        or card.annotations.get("copy_overrides") is not None
    ):
        return None
    spec = compiled_fixed_mana_morph_spec(host, card)
    if spec is None:
        return None
    try:
        validate_morph_face_down_state(card, spec)
        if not current_face_up_has_morph(host, card):
            return None
    except (MorphError, ValueError):
        return None
    return spec


def build_turn_face_up_offer(
    host: MorphActionHost,
    seat: str,
    card: Any,
) -> ActionOffer | None:
    spec = _eligible_spec(host, seat, card)
    if spec is None or not host._cost_is_affordable(
        seat,
        spec.requirements_dict,
        spend_context=None,
    ):
        return None
    return ActionOffer(
        action_id=f"turn-face-up:{card.ref}",
        action="turn_face_up",
        seat=seat,
        label=f"Turn {card.printed_name} face up — {spec.cost_text}",
        expiry_revision=host.state.revision,
        payload=freeze_json(
            {
                "kind": "turn_face_up",
                "card": card.ref,
                "cost": spec.cost_text,
                "requirements": spec.requirements_dict,
                "auto_pay": True,
                "method": MORPH_CAST_METHOD,
            }
        ),
    )


def commit_turn_face_up(
    host: MorphActionHost,
    *,
    seat: str,
    response: Mapping[str, Any],
) -> None:
    host._check_priority(seat)
    ref = response.get("card") or response.get("id")
    if type(ref) is not str or not ref:
        raise GameRuleError("Turn-face-up actions require a card ref")
    card = host._resolve_object(
        seat,
        ref,
        zones={"battlefield"},
        controlled_only=True,
    )
    offer = build_turn_face_up_offer(host, seat, card)
    if offer is None:
        raise GameRuleError("This permanent cannot currently be turned face up")
    supplied = response.get("proposal_fingerprint")
    if supplied is not None:
        expiry = response.get("expiry_revision", offer.expiry_revision)
        if (
            type(expiry) is not int
            or host.state.revision not in {expiry, expiry + 1}
            or str(supplied)
            != replace(offer, expiry_revision=expiry).fingerprint
        ):
            raise GameRuleError("The advertised turn-face-up action is stale")
    spec = _eligible_spec(host, seat, card)
    if spec is None:
        raise GameRuleError("The Morph contract changed before payment")
    clear_mana_undo_stack(host.state.players[seat].stats)
    spent, activations = host._pay_for_cost(
        seat,
        spec.requirements_dict,
        response,
        spend_context=None,
    )
    try:
        turn_card_face_up(card, viewers=tuple(host.seats))
    except ZoneObjectStateError as exc:
        raise GameRuleError(str(exc)) from exc
    host._log(
        seat,
        "permanent.turn_face_up",
        f"{seat} turned {card.ref} {card.printed_name} face up.",
        {
            "object": card.ref,
            "method": MORPH_CAST_METHOD,
            "requirements": spec.requirements_dict,
            "payment": {key: value for key, value in spent.items() if value},
            "mana_sources": [
                activation.get("source_ref") or activation.get("source")
                for activation in activations
            ],
        },
        importance=2,
        changed_objects=[card.object_id],
        changed_players=[seat],
    )
    host._dispatch_semantic_event(
        "permanent.turned_face_up",
        {
            "card": card.ref,
            "object_ref": card.ref,
            "object_id": card.object_id,
            "logical_object_id": card.logical_object_id,
            "controller": card.controller,
        },
        sources=host._semantic_event_sources(zones={"battlefield"}),
    )
    host.turn_priority.complete_special_action(seat)


__all__ = [
    "build_turn_face_up_offer",
    "commit_turn_face_up",
    "MorphActionHost",
]
