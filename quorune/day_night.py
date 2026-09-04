from __future__ import annotations

"""CR 502.2/702.145/731 day-night coordination."""

from typing import Any, Mapping, Protocol, Sequence

from .day_night_model import DayNightBoundMode, DayNightError
from .model import CardInstance, StackItem
from .permanent_transform import (
    commit_transform_batch,
    current_day_night_bound_mode,
)
from .turn_history import previous_turn_spell_cast_counts


class DayNightHost(Protocol):
    state: Any
    active_seats: Sequence[str]

    def _log(
        self,
        actor: str | None,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
        *,
        importance: int = 1,
        changed_objects: Sequence[str] = (),
        changed_players: Sequence[str] = (),
    ) -> Any: ...


def _bound_permanents(
    host: DayNightHost,
) -> tuple[tuple[CardInstance, DayNightBoundMode], ...]:
    values: list[tuple[CardInstance, DayNightBoundMode]] = []
    for card in host.state.cards.values():
        mode = current_day_night_bound_mode(host, card)
        if mode is not None:
            values.append((card, mode))
    return tuple(sorted(values, key=lambda value: value[0].object_id))


def _target_designation(
    current: str | None,
    bounds: Sequence[tuple[CardInstance, DayNightBoundMode]],
) -> str | None:
    if current in {"day", "night"}:
        return current
    if current is not None:
        raise DayNightError("Unknown day/night designation")
    if any(mode is DayNightBoundMode.DAYBOUND for _card, mode in bounds):
        return "day"
    if any(mode is DayNightBoundMode.NIGHTBOUND for _card, mode in bounds):
        return "night"
    return None


def _commit_designation(
    host: DayNightHost,
    designation: str,
    *,
    reason: str,
) -> None:
    if designation not in {"day", "night"}:
        raise DayNightError("Day/night designation is invalid")
    previous = host.state.day_night
    if previous == designation:
        return
    if previous not in {None, "day", "night"}:
        raise DayNightError("Current day/night designation is invalid")
    host.state.day_night = designation
    host._log(
        None,
        "game.day_night",
        f"It became {designation}.",
        {
            "from": previous or "neither",
            "to": designation,
            "reason": reason,
        },
        importance=2,
        changed_players=list(host.active_seats),
    )


def synchronize_day_night(
    host: DayNightHost,
    *,
    reason: str,
    trigger_batch: list[StackItem] | None = None,
) -> tuple[str | None, tuple[str, ...]]:
    """Establish/synchronize the designation and all applicable bound faces."""

    if type(reason) is not str or not reason:
        raise DayNightError("Day/night synchronization requires a reason")
    bounds = _bound_permanents(host)
    designation = _target_designation(host.state.day_night, bounds)
    if designation is None:
        return None, ()
    _commit_designation(host, designation, reason=reason)
    expected_mode = (
        DayNightBoundMode.DAYBOUND
        if designation == "day"
        else DayNightBoundMode.NIGHTBOUND
    )
    mismatched = [
        card for card, mode in bounds if mode is not expected_mode
    ]
    transformed = commit_transform_batch(
        host,
        mismatched,
        reason=reason,
        day_night_instruction=True,
        trigger_batch=trigger_batch,
    )
    return designation, tuple(result.card_ref for result in transformed)


def apply_untap_day_night_transition(
    host: DayNightHost,
    *,
    trigger_batch: list[StackItem],
) -> tuple[str | None, tuple[str, ...]]:
    """Perform CR 502.2 before the active player's untap selection."""

    if host.state.day_night not in {None, "day", "night"}:
        raise DayNightError("Current day/night designation is invalid")
    counts = previous_turn_spell_cast_counts(
        host.state.turn_history,
        current_turn_sequence=host.state.turn_sequence,
    )
    previous_active = (
        host.state.turn_history.previous_active_player
        if host.state.turn_history is not None
        else None
    )
    target = host.state.day_night
    if counts is not None and previous_active is not None:
        active_count = counts.get(previous_active, 0)
        if target == "day" and active_count == 0:
            target = "night"
        elif target == "night" and active_count >= 2:
            target = "day"
    if target is not None:
        _commit_designation(
            host,
            target,
            reason="previous active player's spell count at untap",
        )
    return synchronize_day_night(
        host,
        reason="day/night untap synchronization",
        trigger_batch=trigger_batch,
    )


__all__ = [
    "DayNightHost",
    "apply_untap_day_night_transition",
    "synchronize_day_night",
]
