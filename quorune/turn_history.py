from __future__ import annotations

"""Typed current- and previous-turn history ownership."""

from collections import Counter
from collections.abc import Iterable

from .model import TurnHistory, TurnHistoryEvent, TurnHistoryEventKind


def roll_turn_history(
    history: TurnHistory | None,
    *,
    next_turn_sequence: int,
    previous_active_player: str | None,
) -> TurnHistory | None:
    """Retain one bounded spell-count summary and start the next journal.

    ``None`` is the historical Game Record v3 compatibility mode and stays
    disabled. A mismatched or initial journal starts clean instead of
    inventing previous-turn facts.
    """

    if history is None:
        return None
    if type(next_turn_sequence) is not int or next_turn_sequence < 1:
        raise ValueError("Next turn sequence must be a positive integer")
    if previous_active_player is not None and (
        type(previous_active_player) is not str or not previous_active_player
    ):
        raise ValueError("Previous active player must be nonempty or null")
    if (
        previous_active_player is None
        or history.turn_sequence != next_turn_sequence - 1
        or history.turn_sequence < 1
    ):
        return TurnHistory(turn_sequence=next_turn_sequence)
    counts = Counter(
        event.actor
        for event in history.events
        if event.kind == "spell_cast" and event.actor is not None
    )
    return TurnHistory(
        turn_sequence=next_turn_sequence,
        previous_turn_sequence=history.turn_sequence,
        previous_active_player=previous_active_player,
        previous_spell_cast_counts=dict(sorted(counts.items())),
    )


def previous_turn_spell_cast_counts(
    history: TurnHistory | None,
    *,
    current_turn_sequence: int,
) -> dict[str, int] | None:
    """Return exact prior-turn counts, or ``None`` when unavailable."""

    if (
        history is None
        or history.schema_version != 1
        or history.turn_sequence != current_turn_sequence
        or history.previous_turn_sequence != current_turn_sequence - 1
        or history.previous_active_player is None
    ):
        return None
    return dict(history.previous_spell_cast_counts)


def current_turn_history_events(
    history: TurnHistory | None,
    *,
    turn_sequence: int,
    kind: TurnHistoryEventKind,
) -> tuple[TurnHistoryEvent, ...]:
    if (
        history is None
        or history.schema_version != 1
        or history.turn_sequence != turn_sequence
    ):
        return ()
    return tuple(event for event in history.events if event.kind == kind)


def opponent_was_dealt_damage_this_turn(
    history: TurnHistory | None,
    *,
    turn_sequence: int,
    player: str,
    active_players: Iterable[str],
) -> bool:
    """Return the immutable CR 702.54a look-back fact for ``player``."""

    opponents = frozenset(active_players) - {player}
    return any(
        event.target in opponents and event.amount > 0
        for event in current_turn_history_events(
            history,
            turn_sequence=turn_sequence,
            kind="player_damaged",
        )
    )


__all__ = [
    "current_turn_history_events",
    "opponent_was_dealt_damage_this_turn",
    "previous_turn_spell_cast_counts",
    "roll_turn_history",
]
