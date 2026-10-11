from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from .keyword_abilities import normalized_characteristic_keywords
from .semantic_runtime.player_target_protection import current_player_target_keywords
from .model import CardInstance, GameState
from .protection import (
    ProtectionQueryHost,
    ProtectionVerdict,
    protection_verdict_for_ref,
)
from .target_protection import (
    TargetProtectionSnapshot,
    TargetProtectionVerdict,
    target_protection_verdict,
)


class TargetProtectionEngineQuery(ProtectionQueryHost, Protocol):
    """Narrow read-only compatibility port for authoritative target rows."""

    state: GameState

    def _source_colors_for_ref(
        self, source_ref: str | None
    ) -> set[str]: ...


def target_protection_verdict_for_row(
    host: TargetProtectionEngineQuery,
    *,
    acting_controller: str,
    row: Mapping[str, Any],
    source_ref: str | None,
) -> TargetProtectionVerdict:
    """Materialize one immutable target-protection snapshot from a public row."""

    category = str(row.get("category") or "")
    card = row.get("card")
    target_is_player = category == "player"
    if not target_is_player and not (
        row.get("zone") == "battlefield" and isinstance(card, CardInstance)
    ):
        return TargetProtectionVerdict.ALLOWED

    protected_controller = (
        str(row.get("ref") or "")
        if target_is_player
        else str(row.get("controller") or "")
    )
    player = host.state.players.get(protected_controller)
    characteristics = (
        host._effective_card_data(card)
        if isinstance(card, CardInstance)
        else {"keywords": []}
    )
    snapshot = TargetProtectionSnapshot(
        acting_controller=acting_controller,
        protected_controller=protected_controller,
        target_is_player=target_is_player,
        target_keywords=(current_player_target_keywords(host,protected_controller) if target_is_player else normalized_characteristic_keywords(characteristics)),
        source_colors=frozenset(host._source_colors_for_ref(source_ref)),
        controller_hexproof_colors=frozenset(
            str(value).strip().upper()
            for value in (
                player.stats.get("hexproof_from_colors_until_end", ())
                if player is not None
                else ()
            )
        ),
        player_protection_from_everything=bool(
            target_is_player
            and player is not None
            and player.stats.get(
                "protection_from_everything_until_next_turn"
            )
        ),
        protection_verdict=(
            protection_verdict_for_ref(host, characteristics, source_ref)
            if isinstance(card, CardInstance)
            else ProtectionVerdict.ALLOWED
        ),
    )
    return target_protection_verdict(snapshot)


def player_protection_allows_attachment(
    host: TargetProtectionEngineQuery,
    seat: str,
) -> bool:
    """Apply represented player Protection to live attachment legality."""

    player = host.state.players.get(seat)
    return bool(
        player is not None
        and not player.stats.get(
            "protection_from_everything_until_next_turn"
        )
    )


__all__ = [
    "TargetProtectionEngineQuery",
    "player_protection_allows_attachment",
    "target_protection_verdict_for_row",
]
