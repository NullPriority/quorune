from __future__ import annotations

"""Authoritative knowledge mutation for one inspected player hand."""

from typing import Any

from ..errors import GameRuleError
from ..rules.hand_inspection import InspectHandIntent


def commit_hand_inspection(
    host: Any,
    intent: InspectHandIntent,
) -> tuple[str, ...]:
    """Record exactly one public reveal or controller-private hand look."""

    host._require_seat(intent.actor, in_game=True)
    host._require_seat(intent.player, in_game=True)
    hand_ids = tuple(host.state.players[intent.player].zones["hand"])
    cards = tuple(host.state.cards[object_id] for object_id in hand_ids)
    current_refs = tuple(card.ref for card in cards)
    if len(intent.refs) != len(current_refs) or set(intent.refs) != set(
        current_refs
    ):
        raise GameRuleError("The inspected hand changed")
    viewers = set(host.seats) if intent.public else {intent.actor}
    for card in cards:
        card.known_to = sorted(set(card.known_to).union(viewers))
        if intent.public:
            card.revealed_to = sorted(
                set(card.revealed_to).union(viewers)
            )
    host._log(
        intent.actor,
        "hand.reveal" if intent.public else "hand.look",
        (
            f"{intent.player} revealed their hand."
            if intent.public
            else f"{intent.actor} looked at {intent.player}'s hand."
        ),
        {
            "player": intent.player,
            "count": len(cards),
            "objects": list(intent.refs),
            "public": intent.public,
            "reason": intent.reason,
        },
        visibility=(None if intent.public else [intent.actor, "analyst"]),
        importance=1,
        changed_objects=hand_ids,
        changed_players=[intent.player],
    )
    return intent.refs


__all__ = ["commit_hand_inspection"]
