from __future__ import annotations

"""Finish one Discover procedure without completing its enclosing stack item."""

from .discover_casting import require_discover_exile,require_discover_resolution
from .zone_transitions import ZoneTransitionOwner


def execute_discover_cleanup(host,intent):
    require_discover_resolution(host,actor=intent.actor,stack_ref=intent.stack_ref)
    cards=require_discover_exile(host,intent.progress,actor=intent.actor,include_candidate=False)
    moved=ZoneTransitionOwner(host).move_exiled_cards_to_library_bottom_random(
        tuple(card.object_id for card in cards),owner=intent.actor,
        randomization_key=f'{intent.stack_ref}|discover|{host.state.event_sequence}',reason='Discover',
        replacement_selections=intent.replacement_selections)
    details={'player':intent.actor,'amount':intent.progress.amount,
        'discovered_card':intent.progress.candidate_ref,'exiled_count':len(intent.progress.exiled),
        'bottom_count':len(moved),'source_stack_ref':intent.stack_ref}
    host._log(intent.actor,'library.discover',f'{intent.actor} completed Discover.',details,
        importance=1,changed_objects=[card.object_id for card in cards],changed_players=[intent.actor])
    host._dispatch_semantic_event('player.discovered',details)
