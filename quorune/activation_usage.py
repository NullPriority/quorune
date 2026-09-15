from __future__ import annotations

"""Typed activation-usage limits owned by a permanent incarnation.

Usage state lives on the source object so control changes and phasing preserve
it, while the canonical zone-change reset gives a returning card a new limit.
The owner deliberately knows nothing about Oracle text or card identity.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class ActivationUsageError(ValueError):
    """Activation-usage state or input is malformed."""


ACTIVATION_ONCE_PER_TURN_CAPABILITY = "activation.usage.once_per_turn"


class ActivationLimit(str, Enum):
    ONCE_PER_TURN = "once_per_turn"
    EXHAUST_ONCE = "exhaust_once"


_ONCE_PER_TURN_FIELD = "once_per_turn_activations"
_EXHAUST_FIELD = "exhaust_activations"


@dataclass(frozen=True, slots=True)
class ActivationUsageVerdict:
    available: bool
    reason: str | None = None


def _ability_id(value: Any) -> str:
    if type(value) is not str or not value:
        raise ActivationUsageError("Activation usage requires an ability ID")
    return value


def _annotations(source: Any) -> Mapping[str, Any]:
    value = getattr(source, "annotations", None)
    if not isinstance(value, Mapping):
        raise ActivationUsageError(
            "Activation usage requires source annotations"
        )
    return value


def activation_usage_verdict(
    source: Any,
    *,
    ability_id: str,
    limit: ActivationLimit | None,
    turn_sequence: int,
) -> ActivationUsageVerdict:
    """Return whether one typed usage-limited ability may be activated."""

    identity = _ability_id(ability_id)
    if type(turn_sequence) is not int or turn_sequence < 0:
        raise ActivationUsageError(
            "Activation usage requires a nonnegative turn sequence"
        )
    if limit is None:
        return ActivationUsageVerdict(True)
    annotations = _annotations(source)
    if limit is ActivationLimit.ONCE_PER_TURN:
        raw = annotations.get(_ONCE_PER_TURN_FIELD, {})
        if not isinstance(raw, Mapping) or any(
            type(key) is not str or not key or type(value) is not int
            for key, value in raw.items()
        ):
            raise ActivationUsageError(
                "Once-per-turn activation usage is malformed"
            )
        return (
            ActivationUsageVerdict(False, "already_activated_this_turn")
            if raw.get(identity) == turn_sequence
            else ActivationUsageVerdict(True)
        )
    if limit is ActivationLimit.EXHAUST_ONCE:
        raw = annotations.get(_EXHAUST_FIELD, ())
        if not isinstance(raw, (list, tuple)) or any(
            type(value) is not str or not value for value in raw
        ):
            raise ActivationUsageError("Exhaust activation usage is malformed")
        if len(raw) != len(set(raw)):
            raise ActivationUsageError(
                "Exhaust activation usage contains duplicate abilities"
            )
        return (
            ActivationUsageVerdict(False, "exhaust_ability_already_activated")
            if identity in raw
            else ActivationUsageVerdict(True)
        )
    raise ActivationUsageError(f"Unsupported activation limit {limit!r}")


def commit_activation_usage(
    source: Any,
    *,
    ability_id: str,
    limit: ActivationLimit | None,
    turn_sequence: int,
) -> None:
    """Commit one successful activation through the canonical usage owner."""

    verdict = activation_usage_verdict(
        source,
        ability_id=ability_id,
        limit=limit,
        turn_sequence=turn_sequence,
    )
    if not verdict.available:
        raise ActivationUsageError(
            verdict.reason or "Activation usage limit was already consumed"
        )
    if limit is None:
        return
    annotations = source.annotations
    if limit is ActivationLimit.ONCE_PER_TURN:
        values = dict(annotations.get(_ONCE_PER_TURN_FIELD, {}))
        values[ability_id] = turn_sequence
        annotations[_ONCE_PER_TURN_FIELD] = values
        return
    values = list(annotations.get(_EXHAUST_FIELD, ()))
    values.append(ability_id)
    annotations[_EXHAUST_FIELD] = sorted(values)


__all__ = [
    "ACTIVATION_ONCE_PER_TURN_CAPABILITY",
    "ActivationLimit",
    "ActivationUsageError",
    "ActivationUsageVerdict",
    "activation_usage_verdict",
    "commit_activation_usage",
]
