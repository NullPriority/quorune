from __future__ import annotations

"""Offer and commit represented face-down turn-up special actions."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Protocol

from ..compiled_morph import compiled_fixed_mana_face_down_method_spec
from ..counter_placement import (
    commit_prepared_counter_placements,
    CounterPlacementError,
    CounterPlacementRequest,
    prepare_counter_placements,
)
from ..errors import GameRuleError
from ..mana_undo import clear_mana_undo_stack
from ..morph import (
    current_face_up_has_face_down_method,
    FACE_DOWN_CAST_METHODS,
    MEGAMORPH_CAST_METHOD,
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
    marker = card.annotations.get("face_down_method")
    method = marker.get("kind") if isinstance(marker, Mapping) else None
    if method not in FACE_DOWN_CAST_METHODS:
        return None
    spec = compiled_fixed_mana_face_down_method_spec(
        host,
        card,
        method=method,
    )
    if spec is None:
        return None
    try:
        validate_morph_face_down_state(card, spec)
        if not current_face_up_has_face_down_method(
            host,
            card,
            method=spec.method,
        ):
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
                "method": spec.method,
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
        raise GameRuleError("The face-down method contract changed before payment")
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
    if spec.method == MEGAMORPH_CAST_METHOD:
        payment_id = str(
            response.get("_mana_payment_id") or offer.fingerprint
        )
        event_id = (
            f"counter.place:{payment_id}:{card.ref}:megamorph"
        )
        raw_journal = response.get("_mana_replacement_selections") or {}
        if (
            not isinstance(raw_journal, Mapping)
            or set(raw_journal) - {event_id}
        ):
            raise GameRuleError(
                "The Megamorph replacement journal is malformed"
            )
        selections = raw_journal.get(event_id) or ()
        if not isinstance(selections, (list, tuple)):
            raise GameRuleError(
                "The Megamorph replacement selections are malformed"
            )
        try:
            prepared = prepare_counter_placements(
                host,
                (
                    CounterPlacementRequest(
                        subject_kind="permanent",
                        subject_id=card.object_id,
                        counter_name="+1/+1",
                        amount=1,
                        placing_player=seat,
                        source_ref=card.ref,
                    ),
                ),
                selections=tuple(selections),
                event_ids=(event_id,),
            )
            commit_prepared_counter_placements(
                host,
                prepared,
                reason="Megamorph turn-face-up effect",
            )
        except CounterPlacementError as exc:
            raise GameRuleError(str(exc)) from exc
    elif response.get("_mana_replacement_selections"):
        raise GameRuleError(
            "Turn-face-up replacement selections require Megamorph"
        )
    host._log(
        seat,
        "permanent.turn_face_up",
        f"{seat} turned {card.ref} {card.printed_name} face up.",
        {
            "object": card.ref,
            "method": spec.method,
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
