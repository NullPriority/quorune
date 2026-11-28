"""Resolve one owned-hand exile and its committed draw and play results."""
from collections.abc import Mapping

from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap
from .committed_quantity_followups import CommittedQuantityFollowup, fixed_quantity_effect, quantity_followup_from_count
from .library_selection import LibrarySelectionObjectIdentity
from ..zone_transitions import ZoneTransitionOwner

CAPABILITY = "zone.exile.controller_hand_draw_linked_play"
DEPENDENCIES = (CAPABILITY, "zone.move.fixed_public_set", "zone.change.destination_replacement",
                "zone.draw.library_to_hand", "zone.impulse_access.fixed")
OWNED_EXILE_PLAY_BATCH = "owned_exile_play_batch"


def _effect():
    from ..public_zone_moves import FixedMixedZoneExileSpec, MixedZoneExileScope

    return FixedMixedZoneExileSpec(MixedZoneExileScope.CONTROLLER_HAND_DRAW_AND_PLAY).effect()


def controller_hand_exile_program_dependencies(program):
    from .effect_shape_walkers import nested_effects

    expected = FrozenMap(_effect())
    matches = tuple(effect for effect in nested_effects((program.effects, program.target_schema, program.handlers))
                    if FrozenMap(effect) == expected)
    if not matches and CAPABILITY not in program.capability_dependencies:
        return ()
    if ((program.event, program.active_zone) != ("resolve", "stack") or program.cost_schema is not None
            or program.event_condition is not None or program.target_schema is not None or program.handlers
            or tuple(FrozenMap(effect) for effect in program.effects) != (expected,)):
        raise ValueError("Owned hand exile requires its original resolving spell")
    return DEPENDENCIES


def _resolving_item(host, actor):
    item = host.state.stack[-1] if host.state.stack else None
    if (item is None or item.kind not in {"spell", "spell_copy"} or item.controller != actor
            or item.context.get("currently_resolving") is not True):
        raise GameRuleError("Owned hand exile lost its resolving spell")
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not controller_hand_exile_program_dependencies(program)):
        raise GameRuleError("Owned hand exile lacks its current trusted program")
    return item


def resolve_controller_hand_exile(host, actor, reason, replacement_selections):
    item = _resolving_item(host, actor)
    player = host.state.players[actor]
    origin = {}
    for object_id in player.zones["hand"]:
        card = host.state.cards[object_id]
        if card.owner != actor or card.zone != "hand" or not card.is_card_object:
            raise GameRuleError("Owned hand exile found a card outside the original hand")
        origin[object_id] = card.logical_object_id
    changes = tuple((object_id, "exile") for object_id in origin)
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        changes, reason=reason, log=False, replacement_selections=replacement_selections,
    ) if changes or replacement_selections else ()
    actual = tuple(card for card in moved if card.zone == "exile" and card.owner == actor
                   and card.object_id in origin and card.logical_object_id != origin[card.object_id])
    public = tuple(card for card in actual if not card.face_down)
    host._log(actor, "effect.controller_hand_exile", "Exiled cards from the resolving controller's hand.",
              {"exiled_count": len(actual), "objects": [card.ref for card in public]}, importance=2,
              changed_players=[actor], changed_objects=[card.object_id for card in public])
    draw = fixed_quantity_effect({"op": "draw", "player": "$controller", "count": 1, "private": True})
    effects = quantity_followup_from_count(draw, len(actual)).effects
    if public:
        receipt = {
            "stack": item.ref,
            "objects": [LibrarySelectionObjectIdentity(card.object_id, card.logical_object_id, card.ref).to_dict()
                        for card in public],
        }
        effects += (FrozenMap({"op": "grant_cast_permission", "schema_version": 1, "player": actor,
                               OWNED_EXILE_PLAY_BATCH: receipt}),)
    return CommittedQuantityFollowup(effects)


def grant_hand_exile_play(host, effect, actor):
    from ..impulse_access import grant_owned_next_turn_exile_play

    item = _resolving_item(host, actor)
    payload = effect[OWNED_EXILE_PLAY_BATCH]
    if (set(effect) - {"reason", "_runtime_source"} != {"op", "schema_version", "player", OWNED_EXILE_PLAY_BATCH}
            or effect["op"] != "grant_cast_permission" or type(effect["schema_version"]) is not int
            or effect["schema_version"] != 1 or effect["player"] != actor or not isinstance(payload, Mapping)
            or set(payload) != {"stack", "objects"} or payload["stack"] != item.ref
            or not isinstance(payload["objects"], (list, tuple)) or not payload["objects"]):
        raise GameRuleError("Owned hand exile permission lost its closed committed batch")
    try:
        identities = tuple(LibrarySelectionObjectIdentity.from_dict(value) for value in payload["objects"])
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    if len({value.object_id for value in identities}) != len(identities):
        raise GameRuleError("Owned hand exile permission requires distinct original cards")
    refs = []
    for value in identities:
        card = host.state.cards.get(value.object_id)
        if (card is not None and card.zone == "exile" and not card.face_down and card.owner == actor
                and card.logical_object_id == value.logical_object_id and card.ref == value.ref):
            grant_owned_next_turn_exile_play(host, card=card, actor=actor)
            refs.append(card.ref)
    return tuple(refs)
