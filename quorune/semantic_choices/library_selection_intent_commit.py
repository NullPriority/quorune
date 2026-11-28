"""Commit sealed library selection and maintain its knowledge projection."""

from __future__ import annotations

from ..carddb_characteristics import library_reveal_printed_identity
from ..errors import GameRuleError
from ..private_opponent_library_cast_model import PRIVATE_OPPONENT_LIBRARY_CAST_DESTINATION
from ..rules.library_selection import (
    LibrarySelectionError,
    PRIVATE_PARTITION_BOTTOM_DESTINATION,
    commit_library_selection,
)
from ..rules.committed_quantity_followups import CommittedQuantityFollowup
from ..semantic_runtime.intents import RevealLibraryCardsIntent
from ..semantic_runtime.library_selection_intent import LibrarySelectionIntent


def announce_public_library_prefix(host, intent: RevealLibraryCardsIntent, object_ids: tuple[str, ...]) -> tuple[str, ...]:
    cards = []
    for object_id in object_ids:
        card = host.state.cards[object_id]
        if card.zone != "library" or card.owner != intent.player or card.object_kind != "card":
            raise GameRuleError("A public library prefix requires exact physical library cards")
        name, _ = library_reveal_printed_identity(host.card_db.by_oracle_id(card.oracle_id))
        cards.append({"ref": card.ref, "name": name})
    names = ", ".join(card["name"] for card in cards)
    publication = {"importance": 3} if intent.retain_public_top_visibility else {}
    host._log(intent.actor, "library.reveal", f"{intent.player} revealed {names}.",
              {"player": intent.player, "cards": cards}, changed_objects=object_ids, **publication)
    return intent.refs_top_first

def commit_library_selection_intent(
    self,
    intent: LibrarySelectionIntent,
) -> tuple[str, ...] | CommittedQuantityFollowup:
    try:
        looked = commit_library_selection(
            self,
            actor=intent.actor,
            player=intent.player,
            arrangement=intent.arrangement,
            reason=intent.reason,
            source_stack_ref=intent.source_stack_ref,
            looked_are_public=intent.looked_are_public,
            selected_are_public=intent.selected_are_public,
            replacement_selections=intent.replacement_selections,
            entry_aura_target_ref=intent.entry_aura_target_ref,
            committed_graveyard_treasure=intent.committed_graveyard_treasure,
            **({"require_graveyard_order": True, "graveyard_orders": intent.graveyard_orders}
               if intent.require_graveyard_order else {}),
        )
    except LibrarySelectionError as exc:
        raise GameRuleError(str(exc)) from exc
    update_library_selection_knowledge(self, intent)
    return looked


def update_library_selection_knowledge(self, intent, *, hand_arrivals_only=False):
    from ..library_selection_knowledge_model import remainder_knowledge_viewers
    private_cast = intent.arrangement.selected_destination == PRIVATE_OPPONENT_LIBRARY_CAST_DESTINATION
    viewers = remainder_knowledge_viewers(
        version=1 if private_cast else getattr(self.state,'library_selection_knowledge_version',None),
        player=intent.actor if private_cast else intent.player,
        looked_are_public=intent.looked_are_public, order=intent.arrangement.remainder_order,
        count=len(intent.arrangement.remainder_refs))
    if intent.arrangement.remainder_destination == "library_bottom" and viewers is not None:
        for ref in intent.arrangement.remainder_refs:
            card = self._resolve_object(
                intent.actor,
                ref,
                zones={"library"},
                owned_only=not private_cast,
            )
            card.known_to = list(viewers)
            card.revealed_to = []
    if intent.arrangement.selected_destination == PRIVATE_PARTITION_BOTTOM_DESTINATION:
        for ref in intent.arrangement.selected_refs:
            card = self._resolve_object(intent.actor, ref, zones={"library"}, owned_only=True)
            card.known_to = [intent.actor]
            card.revealed_to = []
    if intent.looked_are_public or intent.selected_are_public:
        viewers = sorted(set(self.seats))
        for ref in intent.arrangement.selected_refs:
            card = self._resolve_object(intent.actor, ref)
            if hand_arrivals_only and card.zone != "hand":
                continue
            card.known_to = sorted(set(card.known_to).union(viewers))
            card.revealed_to = sorted(set(card.revealed_to).union(viewers))


def commit_apnap_library_reveals(host, rounds, *, source_stack_ref, reason, replacement_selections=()):
    from ..rules.library_selection import _validated_objects, place_library_remainder_on_bottom
    from ..zone_transitions import ZoneTransitionOwner

    prepared = tuple(
        (seat, arrangement, _validated_objects(
            host, actor=seat, player=seat, arrangement=arrangement,
            source_stack_ref=source_stack_ref, allow_searched_split=False,
            looked_are_public=False, selected_are_public=True,
        ))
        for seat, arrangement in rounds
    )
    moves = [(cards[ref].object_id, "hand") for _, arrangement, cards in prepared for ref in arrangement.selected_refs]
    if moves:
        ZoneTransitionOwner(host).move_cards_simultaneously(
            moves, reason=reason, log=False, replacement_selections=replacement_selections,
        )
    for seat, arrangement, cards in prepared:
        place_library_remainder_on_bottom(host, seat, source_stack_ref, arrangement, cards)
        update_library_selection_knowledge(host, LibrarySelectionIntent(
            actor=seat, player=seat, arrangement=arrangement, reason=reason, source_stack_ref=source_stack_ref,
            looked_are_public=False, selected_are_public=True,
        ), hand_arrivals_only=True)
        arrived = sum(host.state.cards[cards[ref].object_id].zone == "hand" for ref in arrangement.selected_refs)
        host._log(seat, "library.selection", f"{seat} put {arrived} revealed card(s) into hand.",
                  {"player": seat, "selected_count": len(arrangement.selected_refs), "arrived_count": arrived,
                   "remainder_count": len(arrangement.remainder_refs), "objects": list(arrangement.selected_refs)},
                  changed_players=[seat])


def commit_private_random_library_bottom(host, intent):
    from ..rules.library_selection import commit_random_library_bottom_order

    if not any(
        item.ref == intent.random_source_stack_ref and item.controller == intent.actor
        for item in host.state.stack
    ):
        raise GameRuleError("Random library bottom placement lost its resolving source")
    cards = tuple(host._resolve_object(intent.actor, ref, zones={"library"}, owned_only=True)
                  for ref in intent.refs)
    bottom = commit_random_library_bottom_order(host, intent.player, intent.random_source_stack_ref,
                                                cards, looked_are_public=False)
    host._log(intent.actor, "library.selection", "The remaining cards went to the library bottom.",
              {"player": intent.player, "count": len(cards), "reason": intent.reason},
              visibility=[intent.actor, "analyst"], changed_objects=bottom, importance=1)
    return intent.refs
