"""Commit the private Hideaway partition and retain exact exile look grants."""

from ..hideaway_model import (
    HIDEAWAY_CAPABILITY, HIDEAWAY_LOOK_COUNT, HIDEAWAY_MECHANIC, HIDEAWAY_PAIR, HIDEAWAY_SOURCE_ANNOTATION,
    uses_hideaway_selection, validate_hideaway_selection,
)
from ..linked_ability_exile_state import current_linked_ability_exile, record_linked_ability_exile
from ..linked_exile_return_model import ExiledObjectIdentity
from ..linked_library_imprint_model import library_imprint_source
from ..continuous_effect_model import ContinuousObjectIdentity
from ..zone_transitions import ZoneTransitionOwner
from .effect_shape_walkers import nested_effects
from .library_partition import LibraryPartitionError, commit_ordered_library_partition
from .library_selection import LibrarySelectionError, _shuffle_bottom_selection


HIDEAWAY_KEYWORD_TEMPLATE = "hideaway-private-four-v1"
HIDEAWAY_SELECTION_CAPABILITIES = (
    HIDEAWAY_CAPABILITY, "library.select.fixed_controller", "zone.change.destination_replacement",
    "zone.exile.linked_ability_pair",
)


def hideaway_selection_node_capabilities(*, effects, target_schema):
    if target_schema is not None or len(effects) != 1:
        return ()
    try:
        validate_hideaway_selection(effects[0], player="$controller")
    except (TypeError, ValueError):
        return ()
    return HIDEAWAY_SELECTION_CAPABILITIES


def hideaway_selection_program_dependencies(program):
    payloads = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                     if uses_hideaway_selection(effect))
    if not payloads and HIDEAWAY_MECHANIC not in program.coverage:
        return ()
    required = hideaway_selection_node_capabilities(effects=payloads, target_schema=program.target_schema)
    if (not required or tuple(program.effects) != payloads
            or (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
            or program.provenance.get("template_id") != HIDEAWAY_KEYWORD_TEMPLATE
            or program.handlers or program.cost_schema is not None or program.event_condition is not None):
        raise ValueError("Hideaway requires its exact self-entry trigger and whole private selection")
    return required


def _grant_exile_view(card, viewer):
    if card.face_down and viewer not in card.known_to:
        card.known_to = sorted({*card.known_to, viewer})
        return True
    return False


def grant_hideaway_control_view(host, permanent, controller):
    """Retain a current controller's look access on only the original exile incarnation."""
    if permanent.phased_out or permanent.zone != "battlefield":
        return ()
    source = ContinuousObjectIdentity(permanent.object_id, permanent.logical_object_id)
    return tuple(
        identity.object_id
        for identity in current_linked_ability_exile(host, source=source, ability_pair=HIDEAWAY_PAIR)
        if _grant_exile_view(host.state.cards[identity.object_id], controller)
    )


def commit_hideaway_selection(host, *, actor, player, arrangement, cards, reason,
                             source_stack_ref, looked_are_public, selected_are_public, replacement_selections):
    library = host.state.players[player].zones["library"]
    if (actor != player or looked_are_public or selected_are_public
            or len(arrangement.looked) != min(HIDEAWAY_LOOK_COUNT, len(library))):
        raise LibrarySelectionError("Hideaway requires its original private four-card library window")
    item = next((entry for entry in host.state.stack if entry.ref == source_stack_ref), None)
    try:
        source = library_imprint_source(item, actor=actor)
        program = host.semantics.get(item.semantic_key)
        if (program is None or not host.semantic_program_is_current_trusted(program)
                or not hideaway_selection_program_dependencies(program)):
            raise ValueError("The resolving stack item does not own Hideaway selection")
    except (TypeError, ValueError) as exc:
        raise LibrarySelectionError(str(exc)) from exc
    if not arrangement.looked:
        return ()

    selected, = arrangement.selected_refs
    moved = ZoneTransitionOwner(host).move_card(
        cards[selected].object_id, "exile", reason=reason, semantic_events=True,
        private_library_exile_viewer=actor, private_replacement_journal=True,
        replacement_selections=replacement_selections,
    )
    if moved.zone == "exile":
        record_linked_ability_exile(
            host, source=source, ability_pair=HIDEAWAY_PAIR,
            objects=(ExiledObjectIdentity(moved.object_id, moved.ref, moved.logical_object_id),),
        )
        permanent = host.state.cards.get(source.object_id)
        if (permanent is not None and permanent.zone == "battlefield"
                and permanent.logical_object_id == source.logical_object_id):
            permanent.annotations[HIDEAWAY_SOURCE_ANNOTATION] = source.logical_object_id
            if not permanent.phased_out:
                _grant_exile_view(moved, permanent.controller)

    bottom = [cards[ref].object_id for ref in arrangement.remainder_refs]
    _shuffle_bottom_selection(host, player, source_stack_ref, bottom)
    try:
        commit_ordered_library_partition(library, top_top_first=(), bottom_bottom_first=bottom)
    except LibraryPartitionError as exc:
        raise LibrarySelectionError(str(exc)) from exc
    host._log(
        actor, "library.selection", "Put the rest of the privately viewed cards on the library bottom at random.",
        {"player": player, "looked_count": len(arrangement.looked), "remainder_count": len(bottom),
         "remainder_destination": "library_bottom", "reason": reason},
        visibility=[actor, "analyst"], importance=1,
        changed_objects=[identity.object_id for identity in arrangement.looked], changed_players=[actor],
    )
    return tuple(identity.ref for identity in arrangement.looked)
