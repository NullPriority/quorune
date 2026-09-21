from __future__ import annotations

"""Shared read-only legality for ordinary cast and land source zones."""

from typing import Any

from ..compiled_cast_lifecycles import compiled_fixed_zone_cast_permission
from ..compiled_flashback import (
    compiled_fixed_mana_flashback_spec,
    compiled_ordinary_zone_cast_permission,
)
from ..semantic_runtime.action_permissions import (
    ActionPermissionKind,
    controller_has_action_permission,
    controller_has_library_top_land_permission,
    controller_has_library_top_spell_permission,
)


def compiled_land_play_permission(host: Any, seat: str, card: Any) -> bool:
    permission = host._temporary_play_permission(seat, card)
    if permission is not None and bool(permission.get("allow_land", True)):
        return True
    if card.owner != seat:
        return False
    if card.zone == "hand":
        return True
    if card.zone == "library":
        return controller_has_library_top_land_permission(host, seat, card)
    return bool(
        card.zone == "graveyard"
        and controller_has_action_permission(
            host,
            seat,
            ActionPermissionKind.LAND_PLAY_FROM_OWN_GRAVEYARD,
        )
    )


def compiled_zone_cast_permission(host: Any, seat: str, card: Any) -> bool:
    if compiled_ordinary_zone_cast_permission(host, seat, card):
        return True
    if card.zone == "library" and controller_has_library_top_spell_permission(
        host,
        seat,
        card,
    ):
        return True
    return bool(
        (
            card.owner == seat
            and card.zone == "graveyard"
            and compiled_fixed_mana_flashback_spec(host, card) is not None
        )
        or compiled_fixed_zone_cast_permission(host, seat, card)
    )


__all__ = [
    "compiled_land_play_permission",
    "compiled_zone_cast_permission",
]
