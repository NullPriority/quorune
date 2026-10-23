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
) -> Any:
    options = land_play_permission_options(host, player)
    if not options:
        raise GameRuleError("No land plays remain")
    selected = str(response.get("land_play_permission") or "")
    raw_card=response.get('card') or response.get('id')
    if raw_card:
        from .source_land_plays import source_land_play_choices,SourceLandPlayChoice
        card=host._resolve_object(player,str(raw_card),zones={str(response.get('from') or 'hand')},owned_only=False)
        choices=source_land_play_choices(host,player,card)
        if not choices:raise GameRuleError('No current source permission allows this land play')
        if not selected:return choices[0]
        legacy_selections={'base',*additional_land_play_permission_slots(host,player)}
        if (selected in options or selected in legacy_selections) and any(isinstance(choice,str) for choice in choices):return selected
        match=next((choice for choice in choices if (choice.permission_id if isinstance(choice,SourceLandPlayChoice) else choice)==selected),None)
        if match is None:raise GameRuleError('Selected source land permission is stale')
        return match
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
    selected: Any,
) -> None:
    from .source_land_plays import SourceLandPlayChoice,commit_source_land_choice
    source_choice=selected if isinstance(selected,SourceLandPlayChoice) else None
    if source_choice is not None:selected=source_choice.numeric_permission
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
    if source_choice is not None:commit_source_land_choice(host,player,source_choice)


def reset_additional_land_play_permissions(host: Any, player: str) -> None:
    stats = host.state.players[player].stats
    stats.pop(USED_ADDITIONAL_LAND_PLAY_SLOTS_STAT, None)
    stats.pop(LAND_PLAY_ACCOUNTING_STAT, None)


__all__ = [
    "consume_land_play_permission",
    "reset_additional_land_play_permissions",
    "selected_land_play_permission",
]
