from __future__ import annotations

"""Return a chosen public subset of one committed controller Mill."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap
from .committed_mill_cards import PUBLIC_MILLED_CARDS_CAPABILITY
from .committed_quantity_followups import CommittedQuantityFollowup
from .effect_shape_walkers import nested_effects
from .milled_permanent_battlefield import MilledPermanent


MILL_HAND_CAPABILITY = "zone.mill.committed_creature_land_hand_choice"
MILL_HAND_KIND = "choose_up_to_two_creature_or_land_to_hand"
MILL_HAND_CHOICE = "committed_mill_hand_choice"
MILL_HAND_MOVE = "committed_mill_hand_move"
MILL_COUNT = 4
MAXIMUM = 2
DEPENDENCIES = (
    "zone.mill.fixed", PUBLIC_MILLED_CARDS_CAPABILITY, MILL_HAND_CAPABILITY,
    "zone.change.destination_replacement",
)


def eligible_milled_hand_card(types):
    return bool({"creature", "land"}.intersection(types))


@dataclass(frozen=True, slots=True)
class MillCreatureLandToHand:
    def to_dict(self):
        return {"schema_version": 1, "kind": MILL_HAND_KIND}

    @classmethod
    def from_dict(cls, value):
        result = cls()
        if (not isinstance(value, Mapping) or type(value.get("schema_version")) is not int
                or dict(value) != result.to_dict()):
            raise ValueError("Committed Mill hand subset has an unsupported result")
        return result

    def effect(self):
        return {"op": "mill", "player": "$controller", "count": MILL_COUNT, "followup": self.to_dict()}

    def from_committed_result(self, host, result):
        from ..milling import current_public_milled_cards

        candidates = []
        for card in current_public_milled_cards(host, result):
            types, _, _ = host._type_parts(str(host._effective_card_data(card).get("type_line") or ""))
            if eligible_milled_hand_card(types):
                candidates.append(MilledPermanent(
                    card.ref, card.object_id, card.logical_object_id, card.zone, card.zone_change_counter,
                ).to_dict())
        if not candidates:
            return CommittedQuantityFollowup(())
        return CommittedQuantityFollowup((FrozenMap({
            "op": "choose_option", "player": result.player,
            MILL_HAND_CHOICE: {"schema_version": 1, "candidates": candidates},
        }),))


def uses_milled_creature_land_to_hand(effect):
    followup = effect.get("followup")
    return effect.get("op") == "mill" and isinstance(followup, Mapping) and followup.get("kind") == MILL_HAND_KIND


def mill_hand_node_capabilities(effects, target_schema):
    if len(effects) != 1 or target_schema is not None or not uses_milled_creature_land_to_hand(effects[0]):
        return ()
    effect, = effects
    try:
        spec = MillCreatureLandToHand.from_dict(effect["followup"])
    except (TypeError, ValueError, KeyError):
        return ()
    return DEPENDENCIES if type(effect.get("count")) is int and FrozenMap(effect) == FrozenMap(spec.effect()) else ()


def mill_hand_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers, program.target_schema))
                   if uses_milled_creature_land_to_hand(effect) or MILL_HAND_CHOICE in effect or MILL_HAND_MOVE in effect)
    if not marked and MILL_HAND_CAPABILITY not in program.capability_dependencies:
        return ()
    if (not mill_hand_node_capabilities(marked, None) or program.handlers or program.event_condition is not None
            or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:")):
        raise ValueError("Committed Mill hand selection requires its complete nontargeted spell instruction")
    if tuple(program.effects) == marked and program.target_schema is None and program.cost_schema is None:
        return DEPENDENCIES
    schema = program.target_schema or {}
    modes = schema.get("modes") or {}
    if (not program.effects and isinstance(modes, Mapping) and any(
            tuple(mode.get("effects", ())) == marked and not mode.get("groups")
            for mode in modes.values() if isinstance(mode, Mapping))):
        return DEPENDENCIES
    raise ValueError("Committed Mill hand selection requires one original untargeted spell mode")


def decode_committed_mill_hand_candidates(value):
    if (not isinstance(value, Mapping) or set(value) != {"schema_version", "candidates"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["candidates"], (tuple, list))):
        raise ValueError("Committed Mill hand candidates require their public occurrence receipt")
    candidates = tuple(MilledPermanent.from_dict(row) for row in value["candidates"])
    if (not candidates or len(candidates) > MILL_COUNT
            or len({row.ref for row in candidates}) != len(candidates)
            or len({row.object_id for row in candidates}) != len(candidates)):
        raise ValueError("Committed Mill hand candidates require distinct actual moved cards")
    return candidates


def mill_hand_choice_candidates(effect, *, actor):
    if (set(effect) != {"op", "player", MILL_HAND_CHOICE}
            or effect["op"] != "choose_option" or effect["player"] != actor):
        raise ValueError("Committed Mill hand choice belongs to the original resolving controller")
    return decode_committed_mill_hand_candidates(effect[MILL_HAND_CHOICE])


def move_committed_mill_subset_to_hand(host, effect, *, actor, reason):
    from ..semantic_runtime.zone_move_intent import MoveObjectsSimultaneouslyIntent

    item = host.state.stack[-1] if host.state.stack else None
    selections = effect.get("_replacement_selections", ())
    if (set(effect) - {"op", "player", MILL_HAND_MOVE, "reason", "_runtime_source", "_replacement_selections"}
            or effect.get("player") != actor or not isinstance(selections, (tuple, list))
            or item is None or item.kind not in {"spell", "spell_copy"}
            or item.controller != actor or item.context.get("currently_resolving") is not True):
        raise GameRuleError("Committed Mill hand movement requires its resolving spell")
    try:
        rows = decode_committed_mill_hand_candidates(effect[MILL_HAND_MOVE])
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    if len(rows) > MAXIMUM:
        raise GameRuleError("Committed Mill hand movement exceeds its optional subset")
    cards = []
    for row in rows:
        card = host.state.cards.get(row.object_id)
        if (card is None or card.ref != row.ref or card.zone != row.zone or card.owner != actor
                or card.logical_object_id != row.logical_object_id or not card.is_card_object or card.face_down):
            raise GameRuleError("Committed Mill hand movement lost a selected public card")
        types, _, _ = host._type_parts(str(host._effective_card_data(card).get("type_line") or ""))
        if not eligible_milled_hand_card(types):
            raise GameRuleError("Committed Mill hand movement lost the selected card type")
        cards.append(card)
    return host.move_objects_simultaneously_intent(MoveObjectsSimultaneouslyIntent(
        actor=actor, object_refs=tuple(card.ref for card in cards),
        expected_zones=tuple(dict.fromkeys(card.zone for card in cards)), destination="hand",
        reason=reason, owned_only=True, replacement_selections=tuple(selections),
    ))
