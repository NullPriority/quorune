from __future__ import annotations

"""Canonical current-turn history projection for committed damage."""

from typing import Any, Iterable, Protocol


COMBAT_DAMAGE_HISTORY_MARKER = "history:combat"
COMMANDER_DAMAGE_HISTORY_MARKER = "history:commander"


class DamageTurnHistoryHost(Protocol):
    def _record_turn_history(
        self,
        kind: str,
        *,
        actor: str | None = None,
        object_incarnation: str | None = None,
        target: str | None = None,
        target_kind: str | None = None,
        target_object_incarnation: str | None = None,
        types: Iterable[str] = (),
        amount: int = 0,
    ) -> None: ...


def record_damage_turn_history(
    host: DamageTurnHistoryHost,
    event: Any,
) -> None:
    """Record one committed positive damage result and its source snapshot."""

    host._record_turn_history(
        "player_damaged" if event.target_kind == "player" else "permanent_damaged",
        actor=event.source_controller,
        object_incarnation=event.source_logical_object_id,
        target=event.target,
        target_kind=event.target_kind,
        target_object_incarnation=event.target_logical_object_id,
        types=(
            *event.source_types,
            *event.source_subtypes,
            *((COMBAT_DAMAGE_HISTORY_MARKER,) if event.combat else ()),
            *(
                (COMMANDER_DAMAGE_HISTORY_MARKER,)
                if event.source_is_commander
                else ()
            ),
        ),
        amount=event.dealt_amount,
    )


__all__ = [
    "COMBAT_DAMAGE_HISTORY_MARKER",
    "COMMANDER_DAMAGE_HISTORY_MARKER",
    "DamageTurnHistoryHost",
    "record_damage_turn_history",
]
