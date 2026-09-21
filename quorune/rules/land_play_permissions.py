from __future__ import annotations

"""Authoritative selection and mutation for ordinary land-play quotas."""

from collections.abc import Mapping
from typing import Any

from ..errors import GameRuleError
from ..semantic_runtime.action_permissions import (
    USED_ADDITIONAL_LAND_PLAY_SLOTS_STAT,
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
        if len(options) != 1:
            raise GameRuleError("Choose which land-play permission to use")
        selected = options[0]
    if selected not in options:
        raise GameRuleError("Selected land-play permission is stale")
    return selected


def consume_land_play_permission(
    host: Any,
    player: str,
    selected: str,
) -> None:
    if selected not in land_play_permission_options(host, player):
        raise GameRuleError("Selected land-play permission is stale")
    if selected == "base":
        host.state.players[player].land_plays_remaining -= 1
        return
    stats = host.state.players[player].stats
    used = list(stats.get(USED_ADDITIONAL_LAND_PLAY_SLOTS_STAT, ()))
    used.append(selected)
    stats[USED_ADDITIONAL_LAND_PLAY_SLOTS_STAT] = sorted(set(used))


def reset_additional_land_play_permissions(host: Any, player: str) -> None:
    host.state.players[player].stats.pop(
        USED_ADDITIONAL_LAND_PLAY_SLOTS_STAT,
        None,
    )


__all__ = [
    "consume_land_play_permission",
    "reset_additional_land_play_permissions",
    "selected_land_play_permission",
]
