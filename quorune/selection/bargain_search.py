from __future__ import annotations

"""Retain the actual private search result for its enclosing cast choice."""

from ..discover_model import ExileCastProgress
from ..errors import GameRuleError
from ..rules.bargain_search import BARGAIN_SEARCH_CAST_OPERATION, bargain_search_limit
from ..rules.library_selection import LibrarySelectionObjectIdentity


def move_bargain_search_result(owner, context, cards, replacement_selections):
    if context.seat != context.item.controller or len(cards) > 1:
        raise GameRuleError("Bargain search requires its controller's single library result")
    return [
        owner.move_card(
            card.object_id, "exile", private_library_exile_viewer=context.seat,
            private_replacement_journal=True,
            reason=f"{context.item.label} search", log=False,
            replacement_selections=replacement_selections,
        )
        for card in cards
    ]


def bargain_search_followup(context, moved):
    if len(moved) != 1 or moved[0].zone != "exile":
        return []
    card, = moved
    if card.owner != context.seat or not card.face_down or card.known_to != [context.seat]:
        raise GameRuleError("Bargain search lost its private face-down exile result")
    limit = bargain_search_limit(context.effect)
    identity = LibrarySelectionObjectIdentity(card.object_id, card.logical_object_id, card.ref)
    progress = ExileCastProgress(limit.maximum, (identity,), identity.ref, private_search_parent=context.item.ref)
    return [{"op": BARGAIN_SEARCH_CAST_OPERATION, "player": context.seat, "progress": progress.to_dict()}]
