from __future__ import annotations

"""Commit a typed simultaneous-movement intent through the canonical zone port."""

from typing import Any
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent


def commit_simultaneous_movement(host:Any,intent:MoveObjectsSimultaneouslyIntent)->tuple[str,...]:
    cards = tuple(
        host._resolve_object(
            intent.actor,
            ref,
            zones=set(intent.expected_zones),
            owned_only=intent.owned_only,
            controlled_only=intent.controlled_only,
        )
        for ref in intent.object_refs
    )
    entry={}
    if intent.battlefield_controller is not None:
        from ..public_zone_moves import PublicBattlefieldController
        host._require_seat(intent.actor,in_game=True)
        entry={'tapped':intent.battlefield_tapped,'destination_controllers':{
            card.object_id:card.owner if intent.battlefield_controller is PublicBattlefieldController.OWNER else intent.actor
            for card in cards}}
    host._move_cards_simultaneously(
        [
            (card.object_id, intent.destination)
            for card in cards
        ],
        reason=intent.reason,
        log=False,
        replacement_selections=intent.replacement_selections,
        transition_kinds={
            card.object_id: intent.transition_kind
            for card in cards
        },
        **entry,
    )
    return tuple(card.ref for card in cards)
