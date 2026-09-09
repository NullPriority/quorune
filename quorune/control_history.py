from __future__ import annotations

"""Typed ownership for control-acquisition and upkeep history."""

from typing import Any, Callable, Mapping

from .continuous_effect_state import expire_control_change_continuous_effects
from .model import CONTROL_HISTORY_VERSION


class ControlHistoryError(ValueError):
    """A serialized or proposed control-history value is malformed."""


def record_control_acquisition(
    permanent: Any,
    *,
    controller_turns_begun: int,
    timestamp: int,
    history_version: int | None,
) -> None:
    """Record when the current controller acquired one permanent.

    The turn count remains the canonical CR 302.6 input.  The independent
    timestamp supports rules such as Echo whose look-back boundary is an
    upkeep rather than the beginning of the current turn.
    """

    if type(controller_turns_begun) is not int or controller_turns_begun < 0:
        raise ControlHistoryError(
            "Controller turns-begun count must be a nonnegative integer"
        )
    if type(timestamp) is not int or timestamp < 0:
        raise ControlHistoryError(
            "Control-acquisition timestamp must be a nonnegative integer"
        )
    if history_version not in {None, CONTROL_HISTORY_VERSION}:
        raise ControlHistoryError("Unsupported control-history version")
    permanent.acquired_control_turn_count = controller_turns_begun
    if history_version is not None:
        permanent.acquired_control_timestamp = timestamp


def begin_upkeep_control_epoch(
    player: Any,
    *,
    timestamp: int,
    history_version: int | None,
) -> int:
    """Advance one player's public upkeep boundary and return its predecessor."""

    if type(timestamp) is not int or timestamp < 0:
        raise ControlHistoryError(
            "Upkeep timestamp must be a nonnegative integer"
        )
    if history_version not in {None, CONTROL_HISTORY_VERSION}:
        raise ControlHistoryError("Unsupported control-history version")
    if history_version is None:
        # Historical Game Record v3 journals predate upkeep-relative control
        # history. Replaying them must not introduce a new hashed state field.
        return 0
    previous = getattr(player, "last_upkeep_timestamp", None)
    if type(previous) is not int or previous < 0:
        raise ControlHistoryError(
            "Previous upkeep timestamp must be a nonnegative integer"
        )
    if previous > timestamp:
        raise ControlHistoryError(
            "Upkeep timestamp cannot move backwards"
        )
    player.last_upkeep_timestamp = timestamp
    return previous


def record_battlefield_acquisition(
    state: Any,
    permanent: Any,
    timestamp: int,
) -> None:
    """Record one battlefield object's current control-acquisition facts."""

    controller = permanent.controller
    players = getattr(state, "players", None)
    if (
        not isinstance(players, Mapping)
        or not isinstance(controller, str)
        or controller not in players
    ):
        raise ControlHistoryError(
            "Control acquisition requires a current controller"
        )
    record_control_acquisition(
        permanent,
        controller_turns_begun=players[controller].turns_begun,
        timestamp=timestamp,
        history_version=getattr(state, "control_history_version", None),
    )


def record_control_change(
    state: Any,
    permanent: Any,
    timestamp_factory: Callable[[], int] | None,
    *,
    previous_controller: str,
) -> None:
    """Record a committed control change without perturbing legacy replay."""

    if not isinstance(previous_controller, str) or not previous_controller:
        raise ControlHistoryError(
            "Control changes require the previous controller"
        )
    if previous_controller != permanent.controller:
        expire_control_change_continuous_effects(state, permanent)
    history_version = getattr(state, "control_history_version", None)
    if history_version is not None and timestamp_factory is None:
        raise ControlHistoryError(
            "Current control history requires a timestamp factory"
        )
    timestamp = timestamp_factory() if history_version is not None else 0
    record_battlefield_acquisition(state, permanent, timestamp)


def begin_upkeep_epoch(state: Any, seat: str) -> int:
    """Advance one seat's versioned upkeep-relative history boundary."""

    players = getattr(state, "players", None)
    if (
        not isinstance(players, Mapping)
        or not isinstance(seat, str)
        or seat not in players
    ):
        raise ControlHistoryError("Upkeep history requires a current player")
    return begin_upkeep_control_epoch(
        players[seat],
        timestamp=getattr(state, "timestamp_sequence", None),
        history_version=getattr(state, "control_history_version", None),
    )


def upkeep_trigger_context(
    state: Any,
    phase: str,
    step: str,
    active_player: str,
) -> dict[str, Any]:
    """Build the canonical upkeep event context after advancing history."""

    return {
        "phase": phase,
        "step": step,
        "player": active_player,
        "previous_upkeep_timestamp": begin_upkeep_epoch(
            state, active_player
        ),
    }


__all__ = [
    "CONTROL_HISTORY_VERSION",
    "ControlHistoryError",
    "begin_upkeep_epoch",
    "begin_upkeep_control_epoch",
    "record_battlefield_acquisition",
    "record_control_change",
    "record_control_acquisition",
    "upkeep_trigger_context",
]
