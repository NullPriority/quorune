"""Commit sealed library selection and maintain its knowledge projection."""

from __future__ import annotations

from ..carddb_characteristics import library_reveal_printed_identity
from ..errors import GameRuleError
from ..private_opponent_library_cast_model import PRIVATE_OPPONENT_LIBRARY_CAST_DESTINATION
from ..rules.library_selection import LibrarySelectionError, commit_library_selection
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
    host._log(intent.actor, "library.reveal", f"{intent.player} revealed {names}.",
              {"player": intent.player, "cards": cards}, changed_objects=object_ids)
    return intent.refs_top_first

def commit_library_selection_intent(
    self,
    intent: LibrarySelectionIntent,
) -> tuple[str, ...]:
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
            **({"require_graveyard_order": True, "graveyard_orders": intent.graveyard_orders}
               if intent.require_graveyard_order else {}),
        )
    except LibrarySelectionError as exc:
        raise GameRuleError(str(exc)) from exc
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
    if intent.looked_are_public or intent.selected_are_public:
        viewers = sorted(set(self.seats))
        for ref in intent.arrangement.selected_refs:
            card = self._resolve_object(intent.actor, ref)
            card.known_to = sorted(set(card.known_to).union(viewers))
            card.revealed_to = sorted(set(card.revealed_to).union(viewers))
    return looked


def commit_private_random_library_bottom(host, intent):
    from ..library_selection_knowledge_model import remainder_knowledge_viewers
    from ..rules.library_partition import LibraryPartitionError, commit_ordered_library_partition
    from ..rules.library_selection import _shuffle_bottom_selection

    if not any(
        item.ref == intent.random_source_stack_ref and item.controller == intent.actor
        for item in host.state.stack
    ):
        raise GameRuleError("Random library bottom placement lost its resolving source")
    cards = tuple(host._resolve_object(intent.actor, ref, zones={"library"}, owned_only=True)
                  for ref in intent.refs)
    bottom = [card.object_id for card in cards]
    _shuffle_bottom_selection(host, intent.player, intent.random_source_stack_ref, bottom)
    try:
        commit_ordered_library_partition(host.state.players[intent.player].zones["library"],
                                         top_top_first=(), bottom_bottom_first=bottom)
    except LibraryPartitionError as exc:
        raise GameRuleError(str(exc)) from exc
    viewers = remainder_knowledge_viewers(
        version=getattr(host.state, "library_selection_knowledge_version", None), player=intent.player,
        looked_are_public=False, order="random", count=len(cards),
    )
    if viewers is not None:
        for card in cards:
            card.known_to = list(viewers)
            card.revealed_to = []
    host._log(intent.actor, "library.selection", "The remaining cards went to the library bottom.",
              {"player": intent.player, "count": len(cards), "reason": intent.reason},
              visibility=[intent.actor, "analyst"], changed_objects=bottom, importance=1)
    return intent.refs
