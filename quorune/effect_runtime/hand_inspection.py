from __future__ import annotations

"""Authoritative knowledge mutation for one inspected player hand."""

from typing import Any

from ..errors import GameRuleError
from ..rules.hand_inspection import InspectHandIntent


def validate_entry_hand_reveal(host: Any,selection: Any) -> Any:
    """Preflight one retained private selection before any simultaneous move."""
    from collections.abc import Mapping
    if selection is None or selection=='declined':return None
    fields={'object_id','logical_object_id','ref','player','oracle_id','name'}
    if not isinstance(selection,Mapping) or set(selection)!=fields or any(type(selection[key]) is not str or not selection[key] for key in fields):
        raise GameRuleError('Entry hand reveal identity is malformed')
    actor=selection['player'];card=host.state.cards.get(selection['object_id'])
    if (actor not in host.active_seats or card is None or card.ref!=selection['ref']
            or card.logical_object_id!=selection['logical_object_id'] or card.zone!='hand'
            or card.owner!=actor or card.oracle_id!=selection['oracle_id']
            or card.object_id not in host.state.players[actor].zones['hand']):
        raise GameRuleError('Entry hand reveal lost its original private card')
    return card


def commit_entry_hand_reveal(host: Any,selection: Any,*,source_ref: str) -> None:
    """Publish the preflighted snapshot even when its card entered simultaneously."""
    commit_selected_hand_reveal(host, selection, source_ref=source_ref,
        summary='A card was revealed for battlefield entry.')


def commit_selected_hand_reveal(host: Any, selection: Any, *, source_ref: str, summary: str) -> None:
    """Publish only one selected card, retaining knowledge without a lasting reveal."""
    if selection is None or selection=='declined':return
    actor=selection['player'];card=host.state.cards.get(selection['object_id'])
    if card is not None and card.logical_object_id==selection['logical_object_id']:
        card.known_to=sorted(set(card.known_to).union(host.seats))
    host._log(actor,'card.reveal',summary,
        {'player':actor,'object':selection['ref'],'card':selection['name'],'source':source_ref},
        changed_objects=[selection['object_id']],changed_players=[actor])


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
