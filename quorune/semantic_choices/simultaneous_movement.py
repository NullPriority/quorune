from __future__ import annotations

"""Commit a typed simultaneous-movement intent through the canonical zone port."""

from dataclasses import replace
from typing import Any
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
from ..rules.committed_quantity_followups import CommittedQuantityFollowup


def commit_simultaneous_movement(host:Any,intent:MoveObjectsSimultaneouslyIntent)->tuple[str,...] | CommittedQuantityFollowup:
    from ..semantic_runtime.targeted_graveyard_outcome_intent import TargetedGraveyardExileOutcomeIntent

    if isinstance(intent, TargetedGraveyardExileOutcomeIntent):
        from ..rules.targeted_graveyard_outcomes import commit_targeted_graveyard_exiles, creature_exile_followup

        return creature_exile_followup(intent.result, commit_targeted_graveyard_exiles(host, intent))
    from ..semantic_runtime.graveyard_exile_result_intent import GraveyardExileLifeResultIntent
    if isinstance(intent, GraveyardExileLifeResultIntent):
        from ..rules.graveyard_exile_results import commit_graveyard_exile_life_result
        return commit_graveyard_exile_life_result(host, intent)
    from ..semantic_runtime.graveyard_library_cost_intent import GraveyardLibraryCostBatchIntent
    if isinstance(intent, GraveyardLibraryCostBatchIntent):
        from .graveyard_library_cost import commit_graveyard_library_cost
        return commit_graveyard_library_cost(host, intent)
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
    from ..semantic_runtime.private_hand_entry_intent import PrivateHandEntryIntent
    move_batch = host._move_cards_simultaneously
    if isinstance(intent, PrivateHandEntryIntent):
        from ..errors import GameRuleError
        from ..object_query import object_matches_query, object_query_result
        from ..zone_transitions import ZoneTransitionOwner
        move_batch = ZoneTransitionOwner(host).move_cards_simultaneously
        for card in cards:
            effective = host._effective_card_data(card)
            row = object_query_result(card, effective,
                type_parts=host._type_parts(str(effective.get('type_line') or '')),
                known_to_actor=True, attached_to_ref=None)
            if (card.logical_object_id != intent.selected_identities[card.ref]
                    or not object_matches_query(row, intent.selection.query)):
                raise GameRuleError('Selected hand entry changed its incarnation or predicate')
        entry['aura_target_refs'] = dict(intent.aura_target_refs)
    if intent.library_order is not None:
        by_ref = {card.ref: card.object_id for card in cards}
        entry["library_order"] = replace(
            intent.library_order, members=tuple(by_ref[ref] for ref in intent.library_order.members),
        )
    move_batch(
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
