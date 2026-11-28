"""Commit the private top-library partition through ordinary zone owners."""

from ..linked_ability_exile_state import record_linked_ability_exile
from ..linked_exile_return_model import ExiledObjectIdentity
from ..linked_library_imprint_model import LIBRARY_IMPRINT_PAIR, library_imprint_source
from ..zone_transitions import ZoneTransitionOwner
from .library_partition import LibraryPartitionError, commit_ordered_library_partition
from .library_selection import LibrarySelectionError


def commit_library_imprint(host, *, actor, player, arrangement, cards, reason,
                           source_stack_ref, looked_are_public, selected_are_public,
                           replacement_selections):
    """Move one owner-viewed card face down, seal its link, then bottom the rest."""
    library = host.state.players[player].zones["library"]
    if (player != actor or looked_are_public or selected_are_public
            or len(arrangement.looked) != min(4, len(library))):
        raise LibrarySelectionError("Private library imprint changed its original viewer or whole top set")
    item = next((item for item in host.state.stack if item.ref == source_stack_ref), None)
    try:
        source = library_imprint_source(item, actor=actor)
    except ValueError as exc:
        raise LibrarySelectionError(str(exc)) from exc
    if not arrangement.looked:
        return ()
    selected, = arrangement.selected_refs
    moved = ZoneTransitionOwner(host).move_card(
        cards[selected].object_id, "exile", private_library_exile_viewer=actor,
        reason=reason, semantic_events=True, replacement_selections=replacement_selections,
    )
    if moved.zone == "exile":
        record_linked_ability_exile(
            host, source=source, ability_pair=LIBRARY_IMPRINT_PAIR,
            objects=(ExiledObjectIdentity(moved.object_id, moved.ref, moved.logical_object_id),),
        )
    bottom = [cards[ref].object_id for ref in arrangement.remainder_refs]
    try:
        commit_ordered_library_partition(library, top_top_first=(), bottom_bottom_first=bottom)
    except LibraryPartitionError as exc:
        raise LibrarySelectionError(str(exc)) from exc
    host._log(
        actor, "library.selection", "Finished ordering the rest of the privately viewed cards.",
        {"player": player, "looked_count": len(arrangement.looked), "remainder_count": len(bottom),
         "remainder_destination": "library_bottom", "reason": reason},
        visibility=[actor, "analyst"], importance=1,
        changed_objects=[identity.object_id for identity in arrangement.looked], changed_players=[actor],
    )
    return tuple(identity.ref for identity in arrangement.looked)
