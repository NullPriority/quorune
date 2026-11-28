from __future__ import annotations

"""Finish one Discover procedure without completing its enclosing stack item."""

from .discover_casting import require_discover_exile,require_discover_resolution
from .fixed_token_production import TREASURE_TOKEN_CHARACTERISTICS, predefined_token_identity
from .rules.committed_quantity_followups import fixed_quantity_effect, quantity_followup_from_count
from .zone_transitions import ZoneTransitionOwner


def execute_discover_cleanup(host,intent):
    require_discover_resolution(host, actor=intent.actor, stack_ref=intent.stack_ref, progress=intent.progress)
    cards=require_discover_exile(host,intent.progress,actor=intent.actor,include_candidate=False)
    supplier = intent.progress.supplier
    name_scan = intent.progress.name_scan
    purpose = 'countered-spell-different-name' if name_scan is not None else (
        'exile-until-instant-or-sorcery' if supplier is not None else 'discover'
    )
    reason = 'Exile scan random-bottom cleanup' if supplier is not None or name_scan is not None else 'Discover'
    moved=ZoneTransitionOwner(host).move_exiled_cards_to_library_bottom_random(
        tuple(card.object_id for card in cards),owner=supplier or intent.actor,
        randomization_key=f'{intent.stack_ref}|{purpose}|{host.state.event_sequence}',reason=reason,
        replacement_selections=intent.replacement_selections)
    if supplier is not None or name_scan is not None:
        return
    progress = intent.progress
    discovered = progress.discovered_card
    details={'player':intent.actor,'amount':progress.amount,
        'discovered_card':discovered.ref if discovered is not None else None,'exiled_count':len(progress.exiled),
        'bottom_count':len(moved),'source_stack_ref':intent.stack_ref}
    if progress.recorded_card is not None:
        details['discovered_mana_value'] = progress.recorded_card.mana_value if discovered is not None else None
    host._log(intent.actor,'library.discover',f'{intent.actor} completed Discover.',details,
        importance=1,changed_objects=[card.object_id for card in cards],changed_players=[intent.actor])
    host._dispatch_semantic_event('player.discovered',details)
    if progress.recorded_card is not None:
        return _committed_treasure_difference(details['discovered_mana_value'], progress.amount)


def _committed_treasure_difference(value, limit):
    name, characteristics = predefined_token_identity('Treasure', TREASURE_TOKEN_CHARACTERISTICS)
    token = fixed_quantity_effect({'op': 'create_token', 'controller': '$controller', 'quantity': 1,
        'name': name, 'characteristics': characteristics, 'tapped': True})
    return quantity_followup_from_count(token, limit - value if value is not None else 0)
