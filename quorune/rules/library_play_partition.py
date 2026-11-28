"""A private three-card hand, bottom and face-up exile play partition."""

from collections.abc import Mapping

from ..impulse_access_model import ImpulseAccessDuration
from ..replacement.immutable import thaw_value
from .library_selection import (
    LibrarySelectionError, LibrarySelectionObjectIdentity, PRIVATE_HAND_BOTTOM_EXILE_REMAINDER,
    PRIVATE_PARTITION_BOTTOM_DESTINATION, PRIVATE_PARTITION_EXILE_DESTINATION,
)

PRIVATE_HAND_BOTTOM_EXILE_VERSION = 29
PRIVATE_HAND_BOTTOM_EXILE_CAPABILITY = "library.select.private_hand_bottom_exile_play"
PRIVATE_HAND_BOTTOM_EXILE_DEPENDENCIES = (
    PRIVATE_HAND_BOTTOM_EXILE_CAPABILITY, "library.select.fixed_controller",
    "zone.change.destination_replacement", "zone.impulse_access.chosen_top_exile", "zone.impulse_access.fixed",
)


def private_hand_bottom_exile_effect(player="$controller"):
    return {
        "op": "fixed_library_selection", "schema_version": PRIVATE_HAND_BOTTOM_EXILE_VERSION,
        "player": player, "look_count": 3, "public_reveal": False, "selected_reveal": False,
        "selection_policy": "fixed_any", "minimum": 1, "maximum": 1, "predicate_groups": [],
        "remainder_destination": PRIVATE_HAND_BOTTOM_EXILE_REMAINDER, "remainder_order": "chosen",
    }


def uses_private_hand_bottom_exile(effect):
    return isinstance(effect, Mapping) and effect.get("op") == "fixed_library_selection" and (
        effect.get("schema_version") == PRIVATE_HAND_BOTTOM_EXILE_VERSION
        or effect.get("remainder_destination") == PRIVATE_HAND_BOTTOM_EXILE_REMAINDER
    )


def validate_private_hand_bottom_exile(effect, *, player="$controller"):
    if (
        not isinstance(effect, Mapping) or thaw_value(effect) != private_hand_bottom_exile_effect(player)
        or any(type(effect[field]) is not int for field in ("schema_version", "look_count", "minimum", "maximum"))
        or effect["public_reveal"] is not False or effect["selected_reveal"] is not False
    ):
        raise ValueError("A private hand, bottom and exile partition requires all three fixed roles")


def _require_private_partition_source(host, actor, source_stack_ref):
    item = next((item for item in host.state.stack if item.ref == source_stack_ref), None)
    if (
        item is None or item.controller != actor
        or item.context.get("currently_resolving") is not True
        or (program := host.semantics.get(item.semantic_key)) is None
        or PRIVATE_HAND_BOTTOM_EXILE_CAPABILITY not in program.capability_dependencies
        or not host.semantic_program_is_current_trusted(program)
    ):
        raise LibrarySelectionError("Private hand, bottom and exile requires its current resolving source")


def _grant_private_partition_access(host, actor, committed, reason):
    from ..fixed_top_exile import CHOSEN_IMPULSE_GRANT_FIELD
    from ..impulse_access import apply_chosen_impulse_access

    identity = LibrarySelectionObjectIdentity(committed.object_id, committed.logical_object_id, committed.ref)
    apply_chosen_impulse_access(
        host,
        {"op": "grant_cast_permission", "schema_version": 1, "player": actor,
         CHOSEN_IMPULSE_GRANT_FIELD: {
             "identity": identity.to_dict(), "duration": ImpulseAccessDuration.END_OF_TURN.value,
         }},
        actor=actor, reason=reason,
    )


def commit_private_hand_bottom_exile(
    host, *, actor, player, arrangement, cards, reason, source_stack_ref,
    looked_are_public, selected_are_public, replacement_selections,
):
    from ..zone_transitions import ZoneTransitionOwner
    from .library_partition import LibraryPartitionError, commit_ordered_library_partition

    if actor != player or looked_are_public or selected_are_public:
        raise LibrarySelectionError("Private hand, bottom and exile requires its private resolving controller")
    _require_private_partition_source(host, actor, source_stack_ref)
    library = host.state.players[player].zones["library"]
    primary = arrangement.remainder_destination == PRIVATE_HAND_BOTTOM_EXILE_REMAINDER
    if primary and len(arrangement.looked) != min(3, len(library)):
        raise LibrarySelectionError("The private partition requires the complete original library window")
    if not arrangement.looked:
        return ()
    ref, = arrangement.selected_refs
    card = cards[ref]
    if arrangement.selected_destination == PRIVATE_PARTITION_BOTTOM_DESTINATION:
        try:
            commit_ordered_library_partition(library, top_top_first=(), bottom_bottom_first=(card.object_id,))
        except LibraryPartitionError as exc:
            raise LibrarySelectionError(str(exc)) from exc
    else:
        destination = "exile" if arrangement.selected_destination == PRIVATE_PARTITION_EXILE_DESTINATION else "hand"
        moved = ZoneTransitionOwner(host).move_cards_simultaneously(
            [(card.object_id, destination)], reason=reason, log=False, replacement_selections=replacement_selections,
        )
        if destination == "exile":
            committed = next((arrival for arrival in moved if arrival.object_id == card.object_id), None)
            if committed is not None and committed.zone == "exile" and not committed.face_down:
                _grant_private_partition_access(host, actor, committed, reason)
    if primary:
        host._log(actor, "library.selection", f"{actor} assigned the looked-at cards to the available destinations.",
                  {"player": player, "looked_count": len(arrangement.looked), "reason": reason},
                  importance=1, changed_players=[actor])
    return tuple(identity.ref for identity in arrangement.looked)
