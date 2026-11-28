"""Commit the sealed private choice from an opponent's library."""

from ..linked_library_imprint_model import library_imprint_source
from ..zone_transitions import ZoneTransitionOwner
from .library_partition import LibraryPartitionError, commit_ordered_library_partition
from .library_selection import LibrarySelectionError, _shuffle_bottom_selection


def commit_private_opponent_library_cast(
    host, *, actor, player, arrangement, cards, reason, source_stack_ref,
    looked_are_public, selected_are_public, replacement_selections,
):
    library = host.state.players[player].zones["library"]
    if (actor == player or looked_are_public or selected_are_public
            or len(arrangement.looked) != min(4, len(library))):
        raise LibrarySelectionError("Private opponent library cast access requires its exact opponent and four-card window")
    item = next((entry for entry in host.state.stack if entry.ref == source_stack_ref), None)
    try:
        library_imprint_source(item, actor=actor)
    except ValueError as exc:
        raise LibrarySelectionError(str(exc)) from exc
    if tuple(item.targets) != (player,):
        raise LibrarySelectionError("The library supplier must be the original resolving target")
    if not arrangement.looked:
        return ()

    selected, = arrangement.selected_refs
    ZoneTransitionOwner(host).move_card(
        cards[selected].object_id, "exile", reason=reason, semantic_events=True,
        private_library_exile_viewer=actor, private_library_cast_source=source_stack_ref,
        replacement_selections=replacement_selections,
    )
    bottom = [cards[ref].object_id for ref in arrangement.remainder_refs]
    _shuffle_bottom_selection(host, player, source_stack_ref, bottom)
    try:
        commit_ordered_library_partition(library, top_top_first=(), bottom_bottom_first=bottom)
    except LibraryPartitionError as exc:
        raise LibrarySelectionError(str(exc)) from exc
    host._log(
        actor, "library.selection", "The remaining privately viewed cards went to the library bottom.",
        {"player": player, "looked_count": len(arrangement.looked), "remainder_count": len(bottom),
         "remainder_destination": "library_bottom", "reason": reason},
        visibility=[actor, "analyst"], importance=1,
        changed_objects=[identity.object_id for identity in arrangement.looked], changed_players=[player],
    )
    return tuple(identity.ref for identity in arrangement.looked)
