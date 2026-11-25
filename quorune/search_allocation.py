"""Execute one sealed ordered search assignment through the shared zone owner."""
from .search_allocation_model import SearchAllocationSpec
from .zone_transitions import ZoneTransitionOwner


def move_allocated_search_results(host, context, selected_cards, replacement_selections):
    spec = SearchAllocationSpec.from_effect(context.effect)
    owner = ZoneTransitionOwner(host);seat = context.seat
    object_ids = tuple(card.object_id for card in selected_cards)
    changes = tuple(zip(object_ids, spec.destinations(selected_cards), strict=True))
    owner.reveal_library_search_results(seat, object_ids, public=spec.search.reveal)
    entrants = {oid: seat for oid, dest in changes if dest == 'battlefield'}
    moved = owner.move_cards_simultaneously(changes, reason=f'{context.item.label} search', log=False,
        destination_controllers=entrants, putting_players=entrants,
        tapped_by_object={oid: spec.first_tapped for oid, dest in changes if dest == 'battlefield'},
        replacement_selections=replacement_selections)
    if spec.search.reveal:
        owner.reveal_library_search_results(seat, tuple(c.object_id for c in moved if c.zone == 'hand'), public=True, expected_zone='hand')
    return moved


def allocated_search_result_details(effect, moved):
    if effect.get('schema_version') != 5:return {}
    SearchAllocationSpec.from_effect(effect)
    return {'destinations': {card.ref: card.zone for card in moved}}
