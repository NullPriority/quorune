from __future__ import annotations

"""Commit a typed simultaneous-movement intent through the canonical zone port."""

from dataclasses import replace
from typing import Any
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
from ..semantic_runtime.zone_move_intent import FrontFaceGraveyardCreatureReturnIntent
from ..rules.committed_quantity_followups import CommittedQuantityFollowup, token_followup_from_committed_controllers
from ..semantic_runtime.targeted_exile_token_intent import (
    CommittedTargetExileIntent, OpponentExileDrawIntent, TargetedExileTokensIntent,
)
from ..semantic_runtime.targeted_power_sacrifice_life_intent import TargetedPowerSacrificeLifeIntent
from ..semantic_runtime.counted_sacrifice_token_intent import CountedSacrificeIntent, CountedSacrificeTokenIntent
from ..rules.counted_sacrifice_tokens import committed_sacrifice_count, token_followup_after_sacrifices
from ..rules.any_number_sacrifice import resolve_any_number_sacrifice_followup
from ..rules.targeted_power_sacrifice_life import capture_sacrifice_life_amount, committed_sacrifice_life_amount


def _commit_physical_movement(host, intent, changes, prepared):
    from ..composition_departure_batch import PreparedCompositionDepartureBatch
    from ..composition_departure_commit import commit_prepared_composition_departures
    from ..errors import GameRuleError
    from ..zone_transitions import ZoneTransitionOwner

    if (
        not isinstance(prepared, PreparedCompositionDepartureBatch) or prepared.orders is None
        or tuple(changes) != prepared.changes or prepared.reason != intent.reason
        or any(
            group.actor != intent.actor or group.subject.transition_kind != intent.transition_kind.value
            for group in prepared.groups
        )
    ):
        raise GameRuleError("APNAP physical departures changed before the movement could commit")
    return commit_prepared_composition_departures(ZoneTransitionOwner(host), prepared)


def _graveyard_creature_front_faces(host, cards) -> dict[str, str]:
    """Seal any MDFC's lawful front before entry replacements are measured."""
    from ..errors import GameRuleError

    faces = {}
    for card in cards:
        record = host.card_record(card)
        if record is None or not card.is_card_object:
            raise GameRuleError("Graveyard creature entry requires the original card record")
        if record.layout != "modal_dfc":
            continue
        face = record.faces[0] if record.faces else {}
        name = face.get("name")
        types, _, _ = host._type_parts(str(face.get("type_line") or ""))
        if type(name) is not str or not name or "creature" not in types:
            raise GameRuleError("A returned modal card requires its printed creature front")
        faces[card.object_id] = name
    return faces


def commit_simultaneous_movement(
    host: Any, intent: MoveObjectsSimultaneouslyIntent, *, graveyard_orders=None, require_graveyard_order=False,
    prepared_composition=None,
) -> tuple[str, ...] | CommittedQuantityFollowup | int:
    from ..semantic_runtime.targeted_graveyard_outcome_intent import TargetedGraveyardExileOutcomeIntent

    if isinstance(intent, TargetedGraveyardExileOutcomeIntent):
        from ..rules.targeted_graveyard_outcomes import commit_targeted_graveyard_outcome

        return commit_targeted_graveyard_outcome(host, intent)
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
    counted_origins = ()
    if isinstance(intent, CountedSacrificeIntent):
        from ..errors import GameRuleError

        selected = intent.payload["selected_incarnations"]
        if any(card.phased_out or card.logical_object_id != selected[card.ref] for card in cards):
            raise GameRuleError("A selected counted sacrifice changed its public incarnation")
        counted_origins = tuple((card.object_id, card.logical_object_id) for card in cards)
    sacrifice_life_amount = None
    if isinstance(intent, TargetedPowerSacrificeLifeIntent):
        sacrifice_life_amount = capture_sacrifice_life_amount(host, intent, cards[0])
    entry={}
    if intent.battlefield_controller is not None:
        from ..public_zone_moves import PublicBattlefieldController
        host._require_seat(intent.actor,in_game=True)
        entry={'tapped':intent.battlefield_tapped,'destination_controllers':{
            card.object_id:card.owner if intent.battlefield_controller is PublicBattlefieldController.OWNER else intent.actor
            for card in cards}}
    from ..semantic_runtime.private_hand_entry_intent import PrivateHandEntryIntent
    move_batch = host._move_cards_simultaneously
    if isinstance(intent, FrontFaceGraveyardCreatureReturnIntent):
        faces = _graveyard_creature_front_faces(host, cards)
        if faces:
            from ..zone_transitions import ZoneTransitionOwner

            move_batch = ZoneTransitionOwner(host).move_cards_simultaneously
            entry["entry_faces"] = faces
    if intent.entry_life_payment_refs:
        from ..zone_transitions import ZoneTransitionOwner

        move_batch = ZoneTransitionOwner(host).move_cards_simultaneously
        entry["entry_pay_life"] = {card.object_id: True for card in cards if card.ref in intent.entry_life_payment_refs}
    if isinstance(intent, PrivateHandEntryIntent):
        from ..errors import GameRuleError
        from ..object_query import object_matches_query, object_query_result
        from ..zone_transitions import ZoneTransitionOwner
        move_batch = ZoneTransitionOwner(host).move_cards_simultaneously
        selected_rows = []
        for card in cards:
            effective = host._effective_card_data(card)
            row = object_query_result(card, effective,
                type_parts=host._type_parts(str(effective.get('type_line') or '')),
                known_to_actor=True, attached_to_ref=None)
            if (card.logical_object_id != intent.selected_identities[card.ref]
                    or not object_matches_query(row, intent.selection.query)):
                raise GameRuleError('Selected hand entry changed its incarnation or predicate')
            selected_rows.append(row)
        if any(len(group["refs"]) > group["maximum"] for group in intent.selection.card_type_limits(selected_rows)):
            raise GameRuleError("Selected hand cards no longer fit distinct creature and land slots")
        entry['aura_target_refs'] = dict(intent.aura_target_refs)
    if graveyard_orders is not None or require_graveyard_order:
        from ..errors import GameRuleError
        from ..zone_transitions import ZoneTransitionOwner

        if intent.destination != "graveyard":
            raise GameRuleError("Graveyard order requires a graveyard movement")
        move_batch = ZoneTransitionOwner(host).move_cards_simultaneously
        entry.update(graveyard_orders=graveyard_orders, require_graveyard_order=require_graveyard_order)
    if intent.library_order is not None:
        by_ref = {card.ref: card.object_id for card in cards}
        entry["library_order"] = replace(
            intent.library_order, members=tuple(by_ref[ref] for ref in intent.library_order.members),
        )
    if intent.discard_effect_origin is not None:
        entry["discard_effect_origin"] = intent.discard_effect_origin
    exile_controllers = {}
    if isinstance(intent, CommittedTargetExileIntent):
        exile_controllers = {card.object_id: (card.logical_object_id, card.controller) for card in cards}
    if isinstance(intent, OpponentExileDrawIntent):
        from ..errors import GameRuleError

        controllers = {controller for _identity, controller in exile_controllers.values()}
        if (len(controllers) != len(cards) or intent.actor in controllers
                or not controllers.issubset(host.apnap_order())):
            raise GameRuleError("Opponent exile draws require one live target per opposing controller")
    changes = [(card.object_id, intent.destination) for card in cards]
    if intent.destination == "battlefield":
        entry["putting_players"] = {card.object_id: intent.actor for card in cards if card.zone != "battlefield"}
    if prepared_composition is None:
        moved = move_batch(
            changes, reason=intent.reason, log=False,
            replacement_selections=intent.replacement_selections,
            transition_kinds={card.object_id: intent.transition_kind for card in cards},
            **entry,
        )
    else:
        if entry:
            from ..errors import GameRuleError
            raise GameRuleError("APNAP physical departures require their own complete arrival plan")
        moved = _commit_physical_movement(host, intent, changes, prepared_composition)
    if isinstance(intent, CommittedTargetExileIntent):
        committed = dict.fromkeys(card.object_id for card in moved
            if card.zone == "exile" and card.object_id in exile_controllers
            and card.logical_object_id != exile_controllers[card.object_id][0])
        creators = tuple(exile_controllers[object_id][1] for object_id in committed)
        if isinstance(intent, TargetedExileTokensIntent):
            return token_followup_from_committed_controllers(intent.followup.token, creators)
        from ..rules.opponent_exile_draw_results import opponent_exile_draw_followup

        return opponent_exile_draw_followup(tuple(seat for seat in host.apnap_order() if seat in creators))
    if counted_origins:
        if isinstance(intent, CountedSacrificeTokenIntent):
            return token_followup_after_sacrifices(host, intent.payload["token"], counted_origins)
        return resolve_any_number_sacrifice_followup(
            host, intent.payload["followup"], committed_sacrifice_count(host, counted_origins),
            target=intent.payload.get("target"),
        )
    if sacrifice_life_amount is not None:
        return committed_sacrifice_life_amount(host, intent, cards[0].object_id, sacrifice_life_amount)
    return tuple(card.ref for card in cards)
