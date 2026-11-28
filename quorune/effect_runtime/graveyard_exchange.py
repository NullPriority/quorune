"""Sequence the three canonical zone batches of a resolving graveyard exchange."""

from __future__ import annotations

from ..composition_ordering import resolve_ordered_sacrifice_batch
from ..continuous_effect_state import matching_battlefield_objects
from ..errors import GameRuleError
from ..rules.graveyard_exchange_model import (
    GRAVEYARD_EXCHANGE_FIELD,
    graveyard_exchange_exiled_objects,
    graveyard_exchange_program_dependencies,
    validate_graveyard_exchange_phase,
)
from ..linked_exile_return_model import ExiledObjectIdentity
from ..object_predicate import ObjectQuerySpec
from ..public_zone_moves import (
    PublicZoneDestination, PublicZoneMoveSetSpec, PublicZoneOrigin, PublicZoneRelationAxis,
    snapshot_public_zone_move_set,
)
from ..zone_transitions import ZoneTransitionOwner

_EXILE_GRAVEYARD_CREATURES = PublicZoneMoveSetSpec(
    query=ObjectQuerySpec(zones=("graveyard",), types_all=("creature",), token=False),
    origin=PublicZoneOrigin.GRAVEYARD,
    destination=PublicZoneDestination.EXILE,
    relation_axis=PublicZoneRelationAxis.OWNER,
)


def _require_trusted_exchange(host, item, actor):
    program = host.semantics.get(item.semantic_key)
    if (
        item.kind not in {"spell", "spell_copy"} or item.controller != actor
        or actor not in host.active_seats or item.context.get("currently_resolving") is not True
        or program is None or not host.semantic_program_is_current_trusted(program)
    ):
        raise GameRuleError("A graveyard exchange requires its current trusted resolving spell")
    dependencies = graveyard_exchange_program_dependencies(program)
    if not dependencies or not set(dependencies) <= set(program.capability_dependencies):
        raise GameRuleError("A graveyard exchange requires its complete trusted program")


def resolve_graveyard_exchange_phase(host, item, effect, *, actor, reason):
    instruction = {key: value for key, value in effect.items() if key != "_runtime_source"}
    try:
        validate_graveyard_exchange_phase(instruction)
        _require_trusted_exchange(host, item, actor)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc

    phase = instruction["phase"]
    selections = tuple(instruction.get("_replacement_selections", ()))
    zone_owner = ZoneTransitionOwner(host)
    if phase == "exile":
        return _exile_graveyard_creatures(host, item, zone_owner, selections, actor, reason)

    journal = item.context.get(GRAVEYARD_EXCHANGE_FIELD)
    try:
        exiled = graveyard_exchange_exiled_objects(journal, phase=phase)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc

    if phase == "sacrifice":
        return _sacrifice_creatures(host, zone_owner, journal, selections, actor, reason)

    cards = []
    for identity in exiled:
        card = host.state.cards.get(identity.object_id)
        if identity.matches_exiled_card(card) and card.owner in host.active_seats:
            cards.append(card)
    players = {card.object_id: card.owner for card in cards}
    moved = zone_owner.move_cards_simultaneously(
        tuple((card.object_id, "battlefield") for card in cards), reason=reason,
        destination_controllers=players, putting_players=players,
        replacement_selections=selections, aura_target_refs=instruction.get("_aura_target_refs"),
    )
    journal["next_phase"] = "complete"
    return [card.ref for card in moved if card.zone == "battlefield"]


def _exile_graveyard_creatures(host, item, zone_owner, selections, actor, reason):
    if GRAVEYARD_EXCHANGE_FIELD in item.context:
        raise GameRuleError("The graveyard exchange exile batch already committed")
    snapshot = snapshot_public_zone_move_set(host, actor=actor, spec=_EXILE_GRAVEYARD_CREATURES)
    original = {
        row.object_id: row.logical_object_id
        for row in snapshot.objects
        if host.state.cards[row.object_id].is_card_object
    }
    changes = tuple((object_id, "exile") for object_id in original)
    moved = zone_owner.move_cards_simultaneously(changes, reason=reason, replacement_selections=selections)
    item.context[GRAVEYARD_EXCHANGE_FIELD] = {
        "next_phase": "sacrifice",
        "objects": [
            ExiledObjectIdentity(object_id=card.object_id, logical_object_id=card.logical_object_id, ref=card.ref).to_dict()
            for card in moved
            if card.zone == "exile" and card.logical_object_id != original[card.object_id]
        ],
    }
    return [card.ref for card in moved]


def _sacrifice_creatures(host, zone_owner, journal, selections, actor, reason):
    candidates = matching_battlefield_objects(
        host, ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
    )
    creatures = tuple(card for card in candidates if card.controller in host.active_seats and not card.phased_out)
    moved = resolve_ordered_sacrifice_batch(
        zone_owner, tuple(card.object_id for card in creatures), actor=actor, reason=reason,
        replacement_selections=selections,
    )
    journal["next_phase"] = "return"
    return [card.ref for card in moved]
