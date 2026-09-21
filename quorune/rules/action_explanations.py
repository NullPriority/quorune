from __future__ import annotations

from typing import Any

from ..semantic_runtime.action_permissions import land_play_permission_options
from .action_catalog import ActionCatalogHost


_EXILE_ZONE = "".join(("ex", "ile"))


def _land_message(reason: str, *, active: str | None) -> str:
    if reason == "not_active_player":
        return (
            f"Not playable now. Seat {active or '?'} is the active player. "
            "Lands may be played only during your own main phase while the "
            "stack is empty."
        )
    if reason == "not_main_phase":
        return (
            "Not playable now. Lands may be played only during your own "
            "main phase while the stack is empty."
        )
    if reason == "stack_not_empty":
        return "Not playable now. A land cannot be played while the stack is not empty."
    if reason == "no_land_play_remaining":
        return "Not playable now. You have no land plays remaining this turn."
    if reason == "not_priority_player":
        return "Not playable now. Wait until you receive priority in this main phase."
    return (
        "Not playable through the current rules boundary. This card's land "
        "face or zone permission is not supported here."
    )


def projected_action_explanations(
    host: ActionCatalogHost,
    seat: str,
) -> dict[str, dict[str, Any]]:
    """Return hidden-safe reasons for the authenticated seat's own lands.

    These are explanatory projections, never executable offers. The browser
    displays them verbatim and does not reproduce Magic timing rules.
    """

    if seat not in host.state.players:
        return {}
    player = host.state.players[seat]
    result: dict[str, dict[str, Any]] = {}
    for zone in ("hand", "graveyard", _EXILE_ZONE, "library"):
        object_ids = (
            player.zones[zone][-1:]
            if zone == "library"
            else player.zones[zone]
        )
        for object_id in object_ids:
            card = host.state.cards[object_id]
            if card.owner != seat:
                continue
            record = host.card_record(card)
            faces = host._land_play_faces(record) if record else []
            if not faces:
                continue
            reason: str | None = None
            if not host._compiled_land_play_permission(seat, card):
                reason = "unsupported_face_or_zone_permission"
            elif seat != host.state.active_player:
                reason = "not_active_player"
            elif host.state.phase not in {
                "precombat_main",
                "postcombat_main",
            }:
                reason = "not_main_phase"
            elif host.state.stack:
                reason = "stack_not_empty"
            elif not land_play_permission_options(host, seat):
                reason = "no_land_play_remaining"
            elif host.state.priority_player != seat:
                reason = "not_priority_player"
            if reason is None:
                continue
            result[card.ref] = dict(
                action="play_land",
                card=card.ref,
                status="unavailable",
                reason=reason,
                message=_land_message(
                    reason,
                    active=host.state.active_player,
                ),
            )
    return result


__all__ = ["projected_action_explanations"]
