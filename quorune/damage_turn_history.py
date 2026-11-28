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


def committed_damage_to_player_this_turn(state: Any, recipient: str) -> int | None:
    """Sum the actual final damage results for one current-turn recipient."""
    history = state.turn_history
    if history is None or history.schema_version != 1 or history.turn_sequence != state.turn_sequence:
        return None
    return sum(event.amount for event in history.events
               if event.kind == "player_damaged" and event.target == recipient)


def maximum_committed_damage_this_turn(state: Any) -> int | None:
    """Read the largest single dealt-damage result in the current public journal."""
    history = state.turn_history
    if history is None or history.schema_version != 1 or history.turn_sequence != state.turn_sequence:
        return None
    return max((event.amount for event in history.events
                if event.kind in ("player_damaged", "permanent_damaged")), default=0)


__all__ = [
    "COMBAT_DAMAGE_HISTORY_MARKER",
    "committed_damage_to_player_this_turn",
    "COMMANDER_DAMAGE_HISTORY_MARKER",
    "DamageTurnHistoryHost",
    "maximum_committed_damage_this_turn",
    "record_damage_turn_history",
]
