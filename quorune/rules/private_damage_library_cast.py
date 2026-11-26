"""Authorize the combat-damage selection and its actual private exile arrival."""

from contextvars import ContextVar

from ..errors import GameRuleError
from ..private_opponent_library_cast_model import (
    DAMAGE_LIBRARY_CAST_CAPABILITY, PRIVATE_OPPONENT_LIBRARY_CAST_DESTINATION,
)
from ..semantic_runtime.zone_replacements import prepare_zone_change_replacement_batch
from .graveyard_arrival_order import validate_prepared_graveyard_orders


_DAMAGE_EXILE = ContextVar("damage_library_exile_selection", default=None)


def damage_library_cast_arrangement(arrangement):
    return (arrangement.selected_destination == PRIVATE_OPPONENT_LIBRARY_CAST_DESTINATION
            and arrangement.remainder_destination == "graveyard" and arrangement.remainder_order == "chosen")


def damage_library_exile_move_is_bound(item, card, actor):
    return _DAMAGE_EXILE.get() == (item.ref, actor, card.object_id, card.logical_object_id)


def _require_damage_library_trigger(host, source_stack_ref, actor, player):
    from .private_opponent_library_cast_shapes import private_opponent_library_cast_program_dependencies

    item = next((entry for entry in host.state.stack if entry.ref == source_stack_ref), None)
    if (item is None or item.kind != "triggered_ability" or item.controller != actor or item.targets
            or item.context.get("currently_resolving") is not True
            or item.context.get("event") != "damage.dealt" or item.context.get("combat") is not True
            or item.context.get("target_kind") != "player" or item.context.get("target") != player
            or actor == player or actor not in host.active_seats or player not in host.active_seats):
        raise GameRuleError("Private library selection lost the original combat-damaged player.")
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or DAMAGE_LIBRARY_CAST_CAPABILITY not in private_opponent_library_cast_program_dependencies(program)):
        raise GameRuleError("Private library selection requires its admitted combat damage trigger.")
    return item


def commit_private_damage_library_cast(
    host, *, actor, player, arrangement, cards, reason, source_stack_ref,
    looked_are_public, selected_are_public, replacement_selections, graveyard_orders,
):
    from ..zone_transitions import ZoneTransitionOwner

    item = _require_damage_library_trigger(host, source_stack_ref, actor, player)
    if (not damage_library_cast_arrangement(arrangement) or looked_are_public or selected_are_public
            or len(arrangement.looked) != min(3, len(host.state.players[player].zones["library"]))):
        raise GameRuleError("Private library selection requires the exact original top-three partition.")
    if not arrangement.looked:
        return ()

    chosen, = (cards[ref] for ref in arrangement.selected_refs)
    remainder = tuple((cards[ref].object_id, "graveyard") for ref in arrangement.remainder_refs)
    prepared = prepare_zone_change_replacement_batch(
        host, ((chosen.object_id,"exile"), *remainder), selections=replacement_selections, error_type=GameRuleError,
    )
    grave_preparations = {object_id:prepared[object_id] for object_id, _ in remainder}
    validate_prepared_graveyard_orders(
        graveyard_orders, prepared=grave_preparations, cards=host.state.cards, apnap_order=host.apnap_order(),
    )
    owner = ZoneTransitionOwner(host)
    token = _DAMAGE_EXILE.set((item.ref, actor, chosen.object_id, chosen.logical_object_id))
    try:
        owner.move_card(
            chosen.object_id, "exile", reason=reason, semantic_events=True,
            private_library_exile_viewer=actor, private_library_cast_source=source_stack_ref,
            prepared_replacement=prepared[chosen.object_id],
        )
    finally:
        _DAMAGE_EXILE.reset(token)
    if remainder:
        owner.move_cards_simultaneously(
            remainder, reason=reason, prepared_replacements=grave_preparations,
            graveyard_orders=graveyard_orders, require_graveyard_order=True,
        )
    return tuple(identity.ref for identity in arrangement.looked)
