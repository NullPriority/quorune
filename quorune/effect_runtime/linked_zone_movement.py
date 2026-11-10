"""Resolve moves linked to the original object incarnation."""

from __future__ import annotations

from typing import Any, Mapping

from ..errors import GameRuleError
from ..event_card_return import (
    EVENT_LIBRARY_MOVE_VERSION,
    is_event_library_move,
    validate_event_library_move,
)
from ..impulse_access import grant_temporary_cast_permission
from ..impulse_access_model import TemporaryCastPermissionError, TemporaryCastPermissionGrant
from ..zone_object_cleanup import resolve_zone_object_cleanup_group
from ..zone_trigger_events import ZoneTransitionKind


def apply_move_if_in_zone(
    host: Any, effect: Mapping[str, Any], *, actor: str, operation: str, reason: str,
) -> Any:
    if not is_event_library_move(effect):
        return _apply_move_if_in_zone(host, effect, actor=actor, operation=operation, reason=reason)
    try:
        validate_event_library_move(effect, resolved=True)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    owner = effect["owner"]
    if owner not in host.active_seats:
        return None
    card = next((card for card in host.state.cards.values() if card.ref == effect["card"]), None)
    if card is not None and card.owner != owner:
        raise GameRuleError("The event library owner differs from the departed card owner")
    movement = {key: value for key, value in effect.items() if key not in {EVENT_LIBRARY_MOVE_VERSION, "owner", "shuffle"}}
    moved = _apply_move_if_in_zone(host, movement, actor=actor, operation=operation, reason=reason)
    if effect["shuffle"]:
        host.shuffle_library(owner, reason=reason)
    return moved


def _apply_move_if_in_zone(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
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
    if not expected_zone or card.zone != expected_zone or card.phased_out:
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
    moved = host.move_card(
        card.object_id,
        destination,
        controller=effect.get("controller"),
        tapped=(
            bool(effect["tapped"])
            if "tapped" in effect
            else None
        ),
        position=(
            effect["position"]
            if effect.get("position") is not None
            else "top"
        ),
        reason=reason,
        semantic_events=True,
        transition_kind=(
            ZoneTransitionKind.SACRIFICE
            if effect.get("transition_kind") == "sacrifice"
            else ZoneTransitionKind.ORDINARY
        ),
        replacement_selections=tuple(
            effect.get("_replacement_selections") or ()
        ),
        aura_target_ref=(
            str(effect["_aura_target_ref"])
            if effect.get("_aura_target_ref") is not None
            else None
        ),
    )
    if grant is not None and card.zone == destination:
        grant_temporary_cast_permission(
            host,
            card=card,
            grant=grant,
            reason=reason,
        )
    return moved
