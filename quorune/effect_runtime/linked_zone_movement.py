"""Resolve moves linked to the original object incarnation."""

from __future__ import annotations

from typing import Any, Mapping

from ..errors import GameRuleError
from ..event_card_return import (
    EVENT_LIBRARY_MOVE_VERSION,
    apply_death_source_exile, has_death_source_exile,
    available_event_library_components,
    is_event_hand_return, is_event_library_move,
    validate_event_hand_return, validate_event_library_move,
)
from ..impulse_access import grant_temporary_cast_permission
from ..impulse_access_model import TemporaryCastPermissionError, TemporaryCastPermissionGrant
from ..zone_object_cleanup import resolve_zone_object_cleanup_group
from ..zone_trigger_events import ZoneTransitionKind


def commit_resolved_zone_movement(
    host, card, effect, *, destination, reason, transition_kind,
    discard_origin=None, putting_player=None,
):
    """Apply a resolved movement through the ordinary zone transition owner."""
    if transition_kind is ZoneTransitionKind.SACRIFICE:
        from ..sacrifice_prohibition_model import sacrifice_is_prohibited

        if sacrifice_is_prohibited(host.state, card):
            return None
    return host.move_card(
        card.object_id,
        destination,
        **({"discard_effect_origin": discard_origin} if discard_origin is not None else {}),
        **({"putting_player": putting_player} if putting_player is not None else {}),
        controller=effect.get("controller"),
        tapped=bool(effect["tapped"]) if "tapped" in effect else None,
        position=effect["position"] if effect.get("position") is not None else "top",
        reason=reason,
        semantic_events=True,
        transition_kind=transition_kind,
        replacement_selections=tuple(effect.get("_replacement_selections") or ()),
        aura_target_ref=(str(effect["_aura_target_ref"]) if effect.get("_aura_target_ref") is not None else None),
    )


def _apply_discarded_card_play(host, effect, *, actor, operation, reason):
    from ..fixed_top_exile import CHOSEN_IMPULSE_GRANT_FIELD
    from ..impulse_access import apply_chosen_impulse_access
    from ..impulse_access_model import ImpulseAccessDuration
    from ..rules.discarded_card_exile import discarded_card_play_movement
    from ..rules.library_selection import LibrarySelectionObjectIdentity

    try:
        movement = discarded_card_play_movement(effect, actor=actor)
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    if movement is None:
        return None
    moved = _apply_move_if_in_zone(
        host, movement, actor=actor, operation=operation, reason=reason, required_owner=actor,
    )
    if (moved is not None and moved.zone == "exile" and not moved.face_down and moved.is_card_object
            and moved.zone_change_counter == movement["expected_zone_change_counter"] + 1):
        identity = LibrarySelectionObjectIdentity(
            object_id=moved.object_id, ref=moved.ref, logical_object_id=moved.logical_object_id,
        )
        apply_chosen_impulse_access(host, {
            "op": "grant_cast_permission", "schema_version": 1, "player": actor,
            CHOSEN_IMPULSE_GRANT_FIELD: {
                "identity": identity.to_dict(), "duration": ImpulseAccessDuration.END_OF_TURN.value,
            },
        }, actor=actor, reason=reason)
    return moved


def apply_move_if_in_zone(
    host: Any, effect: Mapping[str, Any], *, actor: str, operation: str, reason: str,
) -> Any:
    from ..rules.committed_mill_cards import MILL_MOVE_FIELD, move_committed_mill_cards_to_hand
    from ..rules.milled_creature_land_to_hand import MILL_HAND_MOVE, move_committed_mill_subset_to_hand
    from ..rules.discarded_card_exile import (
        discarded_card_exile_movement,
        uses_discarded_card_exile,
        DISCARD_PLAY_VERSION,
    )

    if MILL_MOVE_FIELD in effect:
        return move_committed_mill_cards_to_hand(host, effect, actor=actor, reason=reason)
    if MILL_HAND_MOVE in effect:
        return move_committed_mill_subset_to_hand(host, effect, actor=actor, reason=reason)
    if DISCARD_PLAY_VERSION in effect:
        return _apply_discarded_card_play(host, effect, actor=actor, operation=operation, reason=reason)
    if uses_discarded_card_exile(effect):
        try:
            movement = discarded_card_exile_movement(effect, actor=actor)
        except (TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc
        if movement is None:
            return None
        return _apply_move_if_in_zone(
            host, movement, actor=actor, operation=operation, reason=reason, required_owner=actor,
        )
    if has_death_source_exile(effect):
        return apply_death_source_exile(host, effect, actor=actor, reason=reason)
    if is_event_hand_return(effect):
        return _apply_event_hand_return(host, effect, actor=actor, reason=reason)
    if not is_event_library_move(effect):
        return _apply_move_if_in_zone(host, effect, actor=actor, operation=operation, reason=reason)
    try:
        validate_event_library_move(effect, resolved=True)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    owner = effect["owner"]
    if owner not in host.active_seats:
        return None
    components = available_event_library_components(host, effect)
    if components is not None:
        return _apply_library_component_move(host, effect, components, actor=actor, operation=operation, reason=reason)
    card = next((card for card in host.state.cards.values() if card.ref == effect["card"]), None)
    if card is not None and card.owner != owner:
        raise GameRuleError("The event library owner differs from the departed card owner")
    movement = {key: value for key, value in effect.items()
                if key not in {EVENT_LIBRARY_MOVE_VERSION, "owner", "shuffle", "composition", "components"}}
    moved = _apply_move_if_in_zone(host, movement, actor=actor, operation=operation, reason=reason)
    if effect["shuffle"]:
        host.shuffle_library(owner, reason=reason)
    return moved


def _apply_event_hand_return(host, effect, *, actor, reason):
    from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent, ZoneMoveIntent

    try:
        validate_event_hand_return(effect, resolved=True)
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    owner = effect["owner"]
    if owner not in host.active_seats:
        return None
    selections = tuple(effect.get("_replacement_selections", ()))
    components = available_event_library_components(host, effect)
    if components is not None:
        if not components:
            return None
        return host.move_objects_simultaneously_intent(MoveObjectsSimultaneouslyIntent(
            actor=actor, object_refs=tuple(card.ref for card in components),
            expected_zones=("graveyard", "exile"), destination="hand", reason=reason,
            replacement_selections=selections,
        ))
    card = next((card for card in host.state.cards.values() if card.ref == effect["card"]), None)
    if card is None or not card.is_card_object or card.is_token:
        return None
    if card.owner != owner:
        raise GameRuleError("The departure hand return owner differs from the departed card owner")
    return host.move_object_intent(ZoneMoveIntent(
        actor=actor, object_ref=effect["card"], expected_zones=(effect["from"],),
        destination="hand", reason=reason, optional_if_missing=True,
        expected_zone_change_counter=effect["expected_zone_change_counter"], replacement_selections=selections,
    ))


def _apply_library_component_move(host, effect, components, *, actor, operation, reason):
    from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent

    if effect["shuffle"]:
        moved = None
        if components:
            moved = host.move_objects_simultaneously_intent(MoveObjectsSimultaneouslyIntent(
                actor=actor, object_refs=tuple(card.ref for card in components),
                expected_zones=("graveyard", "exile"), destination="library", reason=reason,
                replacement_selections=tuple(effect.get("_replacement_selections") or ()),
            ))
        host.shuffle_library(effect["owner"], reason=reason)
        return moved
    if not components:
        return None
    if len(components) != 1:
        raise GameRuleError("Multiple departed cards require their owner's private library order")
    card, = components
    return _apply_move_if_in_zone(host, {
        "card": card.ref, "from": card.zone, "expected_zone_change_counter": card.zone_change_counter,
        "destination": "library", "position": effect["position"],
        "_replacement_selections": effect.get("_replacement_selections", ()),
    }, actor=actor, operation=operation, reason=reason)


def _apply_move_if_in_zone(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
    required_owner: str | None = None,
) -> Any:
    if "cards" in effect:
        return resolve_zone_object_cleanup_group(
            host,
            effect,
            actor=actor,
            operation=operation,
            reason=reason,
        )
    op = operation
    expected_zone = str(effect.get("from") or "")
    raw_grant = effect.get("then_cast_permission")
    try:
        grant = (
            TemporaryCastPermissionGrant.from_dict(raw_grant)
            if isinstance(raw_grant, Mapping)
            else None
        )
    except TemporaryCastPermissionError as exc:
        raise GameRuleError(str(exc)) from exc
    if raw_grant is not None and grant is None:
        raise GameRuleError("Linked cast permission must be a typed object")
    if grant is not None and grant.player != actor:
        raise GameRuleError("Linked cast-permission player changed")
    try:
        card = host._resolve_object(
            actor,
            str(effect["card"]),
        )
    except GameRuleError:
        host._log(
            actor,
            "effect.linked_object_missing",
            "A linked object no longer exists in the game.",
            {
                "object": str(effect["card"]),
                "expected_zone": expected_zone,
                "reason": "object_absent",
            },
            importance=2,
        )
        return None
    if (not expected_zone or card.zone != expected_zone or card.phased_out
            or required_owner is not None and card.owner != required_owner):
        return None
    expected_counter = effect.get(
        "expected_zone_change_counter"
    )
    expected_identity = effect.get(
        "expected_object_identity"
    )
    identity_mismatch = (
        expected_counter is not None
        and card.zone_change_counter
        != int(expected_counter)
    ) or (
        expected_identity is not None
        and card.logical_object_id
        != str(expected_identity)
    )
    if identity_mismatch:
        host._log(
            actor,
            "effect.linked_object_missing",
            (
                f"{card.ref} is no longer the object linked by "
                "the effect."
            ),
            {
                "object": card.ref,
                "expected_zone": expected_zone,
                "reason": "object_identity_changed",
            },
            importance=2,
            changed_objects=[card.object_id],
        )
        return None
    required_controller = effect.get("required_controller")
    if (
        required_controller is not None
        and card.controller != str(required_controller)
    ):
        return None
    destination = str(effect.get("destination") or "graveyard")
    moved = commit_resolved_zone_movement(
        host, card, effect, destination=destination, reason=reason,
        transition_kind=(ZoneTransitionKind.SACRIFICE if effect.get("transition_kind") == "sacrifice"
                         else ZoneTransitionKind.ORDINARY),
    )
    if grant is not None and moved is not None and card.zone == destination:
        grant_temporary_cast_permission(
            host,
            card=card,
            grant=grant,
            reason=reason,
        )
    return moved
