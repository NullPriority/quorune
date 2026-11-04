from __future__ import annotations

"""Entry before the retained bottom partition through existing mutation owners."""

import random

from .library_partition import commit_ordered_library_partition


def commit_library_battlefield_selection(host,*,actor,player,arrangement,cards,reason,
        source_stack_ref,looked_are_public,selected_are_public,replacement_selections):
    from ..zone_transitions import ZoneTransitionOwner
    from .library_selection import LibrarySelectionError
    if arrangement.remainder_destination!='library_bottom':
        raise LibrarySelectionError('Battlefield selection requires an owned remainder instruction')
    changes=tuple((cards[ref].object_id,'battlefield') for ref in arrangement.selected_refs)
    moved=ZoneTransitionOwner(host).move_cards_simultaneously(changes,reason=reason,log=False,
        tapped=arrangement.entry_tapped,
        destination_controllers={object_id:player for object_id,_ in changes},
        replacement_selections=replacement_selections) if changes else ()
    # Entry replacement preparation must finish before the hidden remainder
    # changes position. A pending entry choice therefore leaves this untouched.
    bottom=[cards[ref].object_id for ref in arrangement.remainder_refs]
    if arrangement.remainder_order=='random':
        random.Random(f'{host.state.config.seed}|{player}|library-battlefield-selection|'
            f'{host.state.event_sequence}|{source_stack_ref}').shuffle(bottom)
    commit_ordered_library_partition(host.state.players[player].zones['library'],
        top_top_first=(),bottom_bottom_first=bottom)
    public_refs=([identity.ref for identity in arrangement.looked] if looked_are_public else
        list(arrangement.selected_refs) if selected_are_public else
        [card.ref for card in moved if card.zone in {'battlefield','graveyard','exile'}])
    host._log(actor,'library.selection',f'{player} completed a library battlefield selection.',
        {'player':player,'looked_count':len(arrangement.looked),
         'selected_count':len(arrangement.selected_refs),'remainder_count':len(arrangement.remainder_refs),
         'selected_destination':'battlefield','entry_tapped':arrangement.entry_tapped,
         'remainder_destination':'library_bottom','objects':public_refs,'reason':reason},
        importance=1,changed_objects=[identity.object_id for identity in arrangement.looked],changed_players=[player])
    return tuple(identity.ref for identity in arrangement.looked)
