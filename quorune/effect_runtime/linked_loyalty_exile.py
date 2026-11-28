"""Resolve the original loyalty abilities' shared exile and entry results."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from ..continuous_effect_model import ContinuousEffectOrigin, ContinuousObjectIdentity
from ..errors import GameRuleError
from ..linked_ability_exile_state import current_linked_ability_exile, exile_batch_for_linked_ability
from ..rules.linked_loyalty_exile_model import (
    LINKED_LOYALTY_CHOICE_OFFERED, LINKED_LOYALTY_EXILE_FIELD, LINKED_LOYALTY_EXILE_PAIR,
    linked_loyalty_entry_identity, linked_loyalty_exile_instruction, validate_linked_loyalty_exile_instruction,
)
from ..rules.entry_subtype_grants import EntrySubtypeGrant
from ..zone_transitions import ZoneTransitionOwner


_ENTRY_PHASE = "enter"
_FRONT_FACE_LAYOUTS = frozenset({"adventure", "flip", "modal_dfc", "prepare", "transform"})


def _original_source(host, item, actor, phase):
    if (
        item is None or item.kind != "activated_ability" or item.controller != actor
        or item.context.get("currently_resolving") is not True or actor not in host.active_seats
    ):
        raise GameRuleError("Linked exile requires its original resolving loyalty ability")
    program = host.semantics.get(item.semantic_key)
    if (
        program is None or program.event != "activate" or program.active_zone != "battlefield"
        or not host.semantic_program_is_current_trusted(program)
        or tuple(program.effects) != (linked_loyalty_exile_instruction(phase),)
    ):
        raise GameRuleError("Linked exile requires its complete trusted loyalty program")
    try:
        return ContinuousObjectIdentity(item.source_object_id, item.context.get("source_logical_object_id"))
    except ValueError as exc:
        raise GameRuleError("Linked exile lost its original source incarnation") from exc


def _linked_creature_candidates(host, item, source):
    value = item.x_value
    if item.targets or type(value) is not int or value < 0:
        raise GameRuleError("Linked exile creature choice requires its announced nonnegative loyalty value")
    eligible = []
    for identity in current_linked_ability_exile(host, source=source, ability_pair=LINKED_LOYALTY_EXILE_PAIR):
        card = host.state.cards[identity.object_id]
        if card.face_down or not card.is_card_object:
            continue
        record = host.card_record(card)
        if record is None:
            continue
        if record.faces and record.layout in _FRONT_FACE_LAYOUTS:
            card = replace(card, active_face=record.faces[0]["name"])
        characteristics = host._effective_card_data(card)
        types, _, _ = host._type_parts(str(characteristics.get("type_line") or ""))
        if "creature" in types and characteristics.get("mana_value") == value:
            eligible.append(identity)
    return tuple(eligible)


def linked_loyalty_choice_refs(host, item, actor, effect):
    from ..rules.linked_creature_settlement import FIELD, choice_refs

    if FIELD in effect:
        return choice_refs(host, item, actor, effect)
    from ..rules.linked_death_upkeep import FIELD as DEATH_UPKEEP_FIELD, choice_refs as death_upkeep_refs

    if DEATH_UPKEEP_FIELD in effect:
        return death_upkeep_refs(host, item, actor, effect)
    instruction = {key: value for key, value in effect.items() if key != LINKED_LOYALTY_CHOICE_OFFERED}
    try:
        phase = validate_linked_loyalty_exile_instruction(instruction)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    if phase != "choose":
        raise GameRuleError("This linked loyalty instruction does not choose a creature")
    source = _original_source(host, item, actor, phase)
    return tuple(identity.ref for identity in _linked_creature_candidates(host, item, source))


def _link_producer_cards(host, item, instruction, actor, phase):
    if phase == "target_top":
        player = instruction["player"]
        if (
            tuple(item.targets) != (player,) or player not in host.active_seats
            or item.context.get("targets_revalidated") is not True
        ):
            raise GameRuleError("Linked top exile requires the original revalidated target")
        library = host.state.players[player].zones["library"]
        return tuple(host.state.cards[object_id] for object_id in reversed(library[-3:]))
    if item.targets:
        raise GameRuleError("Linked opponent-zone exile does not target")
    object_ids = {
        object_id
        for player in host.active_seats if player != actor
        for zone in ("hand", "graveyard")
        for object_id in host.state.players[player].zones[zone]
        if host.state.cards[object_id].is_card_object
    }
    return tuple(host.state.cards[object_id] for object_id in sorted(object_ids))


def apply_linked_loyalty_exile(host, item, effect, *, actor, reason):
    instruction = {
        key: value for key, value in effect.items()
        if key not in {"_runtime_source", "_replacement_selections"}
    }
    selections = effect.get("_replacement_selections", ())
    if not isinstance(selections, (list, tuple)):
        raise GameRuleError("Linked loyalty requires its original replacement selections")
    if instruction.get(LINKED_LOYALTY_EXILE_FIELD) == _ENTRY_PHASE:
        try:
            identity = linked_loyalty_entry_identity(instruction)
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc
        source = _original_source(host, item, actor, "choose")
        if identity not in _linked_creature_candidates(host, item, source):
            raise GameRuleError("The chosen creature no longer matches the original linked exile")
        card = ZoneTransitionOwner(host).move_card(
            identity.object_id, "battlefield", controller=actor, putting_player=actor,
            reason=reason, semantic_events=True, replacement_selections=selections,
            resolution_entry_subtype=EntrySubtypeGrant(
                item.ref, "nightmare", origin=ContinuousEffectOrigin.RESOLUTION,
            ),
        )
        return [card.ref] if card.zone == "battlefield" else []
    try:
        phase = validate_linked_loyalty_exile_instruction(instruction, actor=actor)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    if phase not in {"target_top", "opponent_zones"}:
        raise GameRuleError("Linked loyalty exile requires a producer result")
    source = _original_source(host, item, actor, phase)
    cards = _link_producer_cards(host, item, instruction, actor, phase)
    return list(exile_batch_for_linked_ability(
        host, cards=cards, source=source, ability_pair=LINKED_LOYALTY_EXILE_PAIR,
        reason=reason, replacement_selections=selections,
    ))
