from __future__ import annotations

"""Authoritative selection and mutation for ordinary land-play quotas."""

from collections.abc import Mapping
from typing import Any

from ..errors import GameRuleError
from ..semantic_runtime.action_permissions import (
    LAND_PLAY_ACCOUNTING_STAT,
    LAND_PLAY_ACCOUNTING_VERSION,
    USED_ADDITIONAL_LAND_PLAY_SLOTS_STAT,
    additional_land_play_permission_slots,
    land_play_accounting,
    land_play_permission_options,
)


def selected_land_play_permission(
    host: Any,
    player: str,
    response: Mapping[str, Any],
) -> str:
    options = land_play_permission_options(host, player)
    if not options:
        raise GameRuleError("No land plays remain")
    selected = str(response.get("land_play_permission") or "")
    if not selected:
        return options[0]
    legacy_selections = {
        "base",
        *additional_land_play_permission_slots(host, player),
    }
    if selected not in options and selected not in legacy_selections:
        raise GameRuleError("Selected land-play permission is stale")
    return selected


def consume_land_play_permission(
    host: Any,
    player: str,
    selected: str,
) -> None:
    selected_land_play_permission(
        host,
        player,
        {"land_play_permission": selected},
    )
    state = host.state.players[player]
    base_allowance, played = land_play_accounting(host, player)
    completed = played + 1
    state.stats[LAND_PLAY_ACCOUNTING_STAT] = {
        "version": LAND_PLAY_ACCOUNTING_VERSION,
        "base_allowance": base_allowance,
        "played": completed,
    }
    state.stats.pop(USED_ADDITIONAL_LAND_PLAY_SLOTS_STAT, None)
    state.land_plays_remaining = max(0, base_allowance - completed)


def reset_additional_land_play_permissions(host: Any, player: str) -> None:
    stats = host.state.players[player].stats
    stats.pop(USED_ADDITIONAL_LAND_PLAY_SLOTS_STAT, None)
    stats.pop(LAND_PLAY_ACCOUNTING_STAT, None)


__all__ = [
    "consume_land_play_permission",
    "reset_additional_land_play_permissions",
    "selected_land_play_permission",
]
