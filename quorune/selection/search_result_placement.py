"""Commit ordered public search results and the original land’s post-shuffle untap."""

from ..object_predicate import ObjectQuerySpec
from ..object_query import object_matches_query


def untap_searched_land(host, seat, item, identity):
    object_id, logical_object_id = identity
    card = host.state.cards.get(object_id)
    if (
        card is None or card.zone != "battlefield" or card.phased_out
        or card.logical_object_id != logical_object_id
    ):
        return
    controlled_lands = ObjectQuerySpec(
        zones=("battlefield",), controller=seat, types_all=("land",)
    )
    if sum(
        object_matches_query(row, controlled_lands) for row in host.affected_permanent_object_rows(seat)
    ) >= 4:
        from ..tap_state import set_permanent_tapped

        set_permanent_tapped(
            host, card.ref, actor=seat, tapped=False, reason=f"{item.label} search",
            logical_object_id=logical_object_id,
        )


def move_counted_public_search_batch(owner, seat, cards, destination, item, reveal, replacements):
    if destination == "hand":
        owner.reveal_library_search_results(seat, tuple(card.object_id for card in cards), public=reveal)
    ordered = reversed(cards) if destination == "graveyard" else cards
    moved = owner.move_cards_simultaneously(
        tuple((card.object_id, destination) for card in ordered),
        reason=f"{item.label} search", log=False, replacement_selections=replacements,
    )
    if reveal and destination == "hand":
        owner.reveal_library_search_results(
            seat, tuple(card.object_id for card in moved if card.zone == "hand"), public=True, expected_zone="hand",
        )
    return moved
