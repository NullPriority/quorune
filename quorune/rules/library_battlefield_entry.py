from __future__ import annotations

"""Commit selected entry before the library's remaining instruction."""

import random

from ..continuous_effect_model import ContinuousOperation, Layer
from ..continuous_effect_state import ResolutionEffectSource, create_resolution_continuous_effect
from ..entry_counter_model import EffectEntryCounter
from ..library_battlefield_model import LIBRARY_SHUFFLE_REMAINDER
from .library_partition import commit_ordered_library_partition


def _grant_entry_hexproof(host, *, actor, source_stack_ref, moved, looked):
    original = {identity.object_id: identity.logical_object_id for identity in looked}
    entered = tuple(card for card in moved if card.zone == "battlefield"
                    and original[card.object_id] != card.logical_object_id)
    if not entered:
        return
    effect = create_resolution_continuous_effect(
        host, source=ResolutionEffectSource(source_stack_ref), targets=entered, layer=Layer.ABILITY, sublayer="6",
        operations=(ContinuousOperation("add_ability", "Hexproof"),),
        expires_on_next_turn_of=actor,
    )
    if effect is None:
        from .library_selection import LibrarySelectionError

        raise LibrarySelectionError("The revealed entry followup requires the continuous-effect journal")


def _finish_library_remainder(host, owner, *, actor, player, arrangement, cards, moved, source_stack_ref, reason):
    if arrangement.remainder_destination == LIBRARY_SHUFFLE_REMAINDER:
        _grant_entry_hexproof(host, actor=actor, source_stack_ref=source_stack_ref, moved=moved, looked=arrangement.looked)
        owner.shuffle_library(player, reason=reason, actor=actor)
        return
    bottom = [cards[ref].object_id for ref in arrangement.remainder_refs]
    if arrangement.remainder_order == "random":
        random.Random(f"{host.state.config.seed}|{player}|library-battlefield-selection|"
                      f"{host.state.event_sequence}|{source_stack_ref}").shuffle(bottom)
    commit_ordered_library_partition(host.state.players[player].zones["library"],
                                     top_top_first=(), bottom_bottom_first=bottom)


def commit_library_battlefield_selection(host,*,actor,player,arrangement,cards,reason,
        source_stack_ref,looked_are_public,selected_are_public,replacement_selections):
    from ..zone_transitions import ZoneTransitionOwner
    from .library_selection import LibrarySelectionError
    if arrangement.remainder_destination not in ("library_bottom", LIBRARY_SHUFFLE_REMAINDER):
        raise LibrarySelectionError('Battlefield selection requires an owned remainder instruction')
    followup = arrangement.entry_followup
    if followup is not None and (actor != player or not looked_are_public or selected_are_public):
        raise LibrarySelectionError("The revealed entry followup requires its controller's public inspection")
    changes=tuple((cards[ref].object_id,'battlefield') for ref in arrangement.selected_refs)
    owner = ZoneTransitionOwner(host)
    entry_counters = ({object_id: (EffectEntryCounter("+1/+1", followup.counters, player, source_stack_ref, "122.6a"),)
                       for object_id, _ in changes} if followup is not None else None)
    moved=owner.move_cards_simultaneously(changes,reason=reason,log=False,
        tapped=arrangement.entry_tapped,
        destination_controllers={object_id:player for object_id,_ in changes},
        effect_entry_counters=entry_counters,
        replacement_selections=replacement_selections) if changes else ()
    _finish_library_remainder(host, owner, actor=actor, player=player, arrangement=arrangement,
                              cards=cards, moved=moved, source_stack_ref=source_stack_ref, reason=reason)
    public_refs=([identity.ref for identity in arrangement.looked] if looked_are_public else
        list(arrangement.selected_refs) if selected_are_public else
        [card.ref for card in moved if card.zone in {'battlefield','graveyard','exile'}])
    host._log(actor,'library.selection',f'{player} completed a library battlefield selection.',
        {'player':player,'looked_count':len(arrangement.looked),
         'selected_count':len(arrangement.selected_refs),'remainder_count':len(arrangement.remainder_refs),
         'selected_destination':'battlefield','entry_tapped':arrangement.entry_tapped,
         'remainder_destination':arrangement.remainder_destination,'objects':public_refs,'reason':reason},
        importance=1,changed_objects=[identity.object_id for identity in arrangement.looked],changed_players=[player])
    return tuple(identity.ref for identity in arrangement.looked)
