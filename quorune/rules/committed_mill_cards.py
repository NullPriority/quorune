"""Resolve a public card subset from the exact committed Mill result."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..adventure import ADVENTURE_CAPABILITY, has_adventure_card_face
from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap
from .committed_quantity_followups import CommittedQuantityFollowup
from .effect_shape_walkers import nested_effects


PUBLIC_MILLED_CARDS_CAPABILITY = "zone.mill.committed_public_cards"
MILL_ADVENTURES_TO_HAND_CAPABILITY = "zone.mill.committed_adventures_to_hand"
ADVENTURE_MILL_KIND = "all_adventure_to_hand"
ADVENTURE_MILL_COUNT = 7
MILL_MOVE_FIELD = "committed_mill_move"


@dataclass(frozen=True, slots=True)
class MillAdventuresToHand:
    """The mandatory all-Adventures result of a controller's fixed Mill."""

    def to_dict(self):
        return {"schema_version": 1, "kind": ADVENTURE_MILL_KIND}

    @classmethod
    def from_dict(cls, value):
        result = cls()
        if (
            not isinstance(value, Mapping)
            or type(value.get("schema_version")) is not int
            or dict(value) != result.to_dict()
        ):
            raise ValueError("A committed Mill Adventure result is malformed")
        return result

    def effect(self):
        return {
            "op": "mill", "player": "$controller", "count": ADVENTURE_MILL_COUNT,
            "followup": self.to_dict(),
        }

    @property
    def dependencies(self):
        return (
            "zone.mill.fixed", PUBLIC_MILLED_CARDS_CAPABILITY,
            MILL_ADVENTURES_TO_HAND_CAPABILITY, "zone.change.destination_replacement",
            ADVENTURE_CAPABILITY,
        )

    def from_committed_result(self, host, result):
        from ..milling import current_public_milled_cards

        cards = tuple(
            {
                "object_id": card.object_id, "logical_object_id": card.logical_object_id,
                "zone": card.zone,
            }
            for card in current_public_milled_cards(host, result)
            if has_adventure_card_face(host.card_record(card))
        )
        if not cards:
            return CommittedQuantityFollowup(())
        return CommittedQuantityFollowup((FrozenMap({
            "op": "move_if_in_zone",
            MILL_MOVE_FIELD: 1,
            "player": result.player,
            "cards": cards,
        }),))


def uses_committed_mill_result(effect):
    followup = effect.get("followup")
    return isinstance(followup, Mapping) and followup.get("kind") == ADVENTURE_MILL_KIND


def committed_mill_card_node_capabilities(effects, target_schema):
    if len(effects) != 1 or target_schema is not None:
        return ()
    effect, = effects
    try:
        result = MillAdventuresToHand.from_dict(effect["followup"])
    except (TypeError, ValueError, KeyError):
        return ()
    if type(effect.get("count")) is not int or dict(effect) != result.effect():
        return ()
    return result.dependencies


def committed_mill_card_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers, program.target_schema))
                   if uses_committed_mill_result(effect) or MILL_MOVE_FIELD in effect)
    if not marked and MILL_ADVENTURES_TO_HAND_CAPABILITY not in program.capability_dependencies:
        return ()
    required = committed_mill_card_node_capabilities(program.effects, program.target_schema)
    if (
        not required or marked != tuple(program.effects) or program.handlers or program.event_condition is not None
        or (program.event, program.active_zone) != ("resolve", "stack")
        or not program.ability_id.startswith("spell:")
    ):
        raise ValueError("Committed Mill Adventures require the exact original fixed-count spell")
    return required


def is_closed_committed_mill_card_program(program):
    try:
        required = committed_mill_card_program_dependencies(program)
    except ValueError:
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def _current_mill_move_cards(host, snapshots, player):
    from ..zone_transition_model import PUBLIC_ZONES

    if not isinstance(snapshots, (list, tuple)) or not snapshots:
        raise GameRuleError("Committed Mill movement requires a nonempty identity group")
    result = []
    seen = set()
    for row in snapshots:
        if (
            not isinstance(row, Mapping) or set(row) != {"object_id", "logical_object_id", "zone"}
            or any(type(value) is not str or not value for value in row.values())
            or row["object_id"] in seen or row["zone"] not in PUBLIC_ZONES
        ):
            raise GameRuleError("Committed Mill movement has malformed public identities")
        object_id = row["object_id"]
        seen.add(object_id)
        card = host.state.cards.get(object_id)
        if (
            card is not None and card.zone == row["zone"] and card.owner == player
            and card.logical_object_id == row["logical_object_id"] and card.is_card_object
            and not card.face_down and has_adventure_card_face(host.card_record(card))
        ):
            result.append(card)
    return tuple(result)


def move_committed_mill_cards_to_hand(host, effect, *, actor, reason):
    """Resume the exact captured group through ordinary simultaneous movement."""

    from ..semantic_runtime.zone_move_intent import MoveObjectsSimultaneouslyIntent

    item = host.state.stack[-1] if host.state.stack else None
    selections = effect.get("_replacement_selections", ())
    if (
        set(effect) - {"op", MILL_MOVE_FIELD, "player", "cards", "reason", "_runtime_source", "_replacement_selections"}
        or type(effect.get(MILL_MOVE_FIELD)) is not int or effect[MILL_MOVE_FIELD] != 1
        or effect.get("player") != actor or not isinstance(selections, (list, tuple))
        or item is None or item.kind not in {"spell", "spell_copy"}
        or item.controller != actor or not item.context.get("currently_resolving")
    ):
        raise GameRuleError("Committed Mill movement requires its resolving controller")
    cards = _current_mill_move_cards(host, effect.get("cards"), actor)
    if not cards:
        return ()
    return host.move_objects_simultaneously_intent(MoveObjectsSimultaneouslyIntent(
        actor=actor, object_refs=tuple(card.ref for card in cards),
        expected_zones=tuple(dict.fromkeys(card.zone for card in cards)),
        destination="hand", reason=reason, owned_only=True,
        replacement_selections=tuple(selections),
    ))
