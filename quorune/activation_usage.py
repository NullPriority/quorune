from __future__ import annotations

"""Typed ability-usage limits owned by a permanent incarnation.

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
ACTIVATION_ONCE_PER_OBJECT_CAPABILITY='activation.usage.once_per_object'


class ActivationLimit(str, Enum):
    ONCE_PER_OBJECT='once_per_object'
    ONCE_PER_TURN = "once_per_turn"
    EXHAUST_ONCE = "exhaust_once"
    POWER_UP_ONCE = "power_up_once"


_ONCE_PER_TURN_FIELD = "once_per_turn_activations"
_BOAST_SECOND_FIELD = "second_boast_activations"
_EXHAUST_FIELD = "exhaust_activations"
_ONCE_PER_OBJECT_FIELD='once_per_object_activations'
_TRIGGER_ONCE_PER_TURN_FIELD = "once_per_turn_triggers"
_CAST_GRANT_ONCE_PER_TURN_FIELD = "once_per_turn_cast_grants"


def cast_grant_usage_available(source: Any, *, ability_id: str, turn_sequence: int) -> bool:
    """Read a static casting permission's allowance for this source incarnation."""
    identity = _ability_id(ability_id)
    if type(turn_sequence) is not int or turn_sequence < 0:
        raise ActivationUsageError("Casting permission usage requires a nonnegative turn")
    raw = _annotations(source).get(_CAST_GRANT_ONCE_PER_TURN_FIELD, {})
    if not isinstance(raw, Mapping) or any(type(k) is not str or not k or type(v) is not int or v < 0 for k, v in raw.items()):
        raise ActivationUsageError("Once-per-turn casting permission usage is malformed")
    return raw.get(identity) != turn_sequence


def commit_cast_grant_usage(source: Any, *, ability_id: str, turn_sequence: int) -> None:
    """Consume one successfully used permission through the existing usage owner."""
    if not cast_grant_usage_available(source, ability_id=ability_id, turn_sequence=turn_sequence):
        raise ActivationUsageError("Casting permission usage was already consumed")
    values = dict(source.annotations.get(_CAST_GRANT_ONCE_PER_TURN_FIELD, {}))
    values[ability_id] = turn_sequence
    source.annotations[_CAST_GRANT_ONCE_PER_TURN_FIELD] = values


def trigger_usage_available(source: Any, *, ability_id: str, turn_sequence: int) -> bool:
    """Read one intrinsic triggered ability's current-turn allowance."""

    identity = _ability_id(ability_id)
    if type(turn_sequence) is not int or turn_sequence < 0:
        raise ActivationUsageError("Trigger usage requires a nonnegative turn sequence")
    raw = _annotations(source).get(_TRIGGER_ONCE_PER_TURN_FIELD, {})
    if not isinstance(raw, Mapping) or any(
        type(key) is not str or not key or type(value) is not int or value < 0
        for key, value in raw.items()
    ):
        raise ActivationUsageError("Once-per-turn trigger usage is malformed")
    return raw.get(identity) != turn_sequence


def commit_trigger_usage(source: Any, *, ability_id: str, turn_sequence: int) -> None:
    """Consume an allowance when the ability triggers, before stack placement."""

    if not trigger_usage_available(source, ability_id=ability_id, turn_sequence=turn_sequence):
        raise ActivationUsageError("Trigger usage limit was already consumed")
    values = dict(source.annotations.get(_TRIGGER_ONCE_PER_TURN_FIELD, {}))
    values[ability_id] = turn_sequence
    source.annotations[_TRIGGER_ONCE_PER_TURN_FIELD] = values


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


def _second_boast_uses(annotations):
    raw = annotations.get(_BOAST_SECOND_FIELD, {})
    if not isinstance(raw, Mapping) or any(type(key) is not str or not key or type(turn) is not int or turn < 0
                                           for key, turn in raw.items()):
        raise ActivationUsageError("Second boast usage is malformed")
    return raw


def activation_usage_verdict(
    source: Any,
    *,
    ability_id: str,
    limit: ActivationLimit | None,
    turn_sequence: int,
    maximum: int = 1,
) -> ActivationUsageVerdict:
    """Return whether one typed usage-limited ability may be activated."""

    identity = _ability_id(ability_id)
    if type(turn_sequence) is not int or turn_sequence < 0:
        raise ActivationUsageError(
            "Activation usage requires a nonnegative turn sequence"
        )
    if type(maximum) is not int or maximum not in {1, 2} or (
            maximum == 2 and limit is not ActivationLimit.ONCE_PER_TURN):
        raise ActivationUsageError("A second use requires its once-per-turn activation limit")
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
        if maximum == 2 and raw.get(identity) == turn_sequence and _second_boast_uses(annotations).get(identity) != turn_sequence:
            return ActivationUsageVerdict(True)
        return (
            ActivationUsageVerdict(False, "already_activated_this_turn")
            if raw.get(identity) == turn_sequence
            else ActivationUsageVerdict(True)
        )
    if limit in {ActivationLimit.EXHAUST_ONCE,ActivationLimit.POWER_UP_ONCE,ActivationLimit.ONCE_PER_OBJECT}:
        field=_ONCE_PER_OBJECT_FIELD if limit is ActivationLimit.ONCE_PER_OBJECT else _EXHAUST_FIELD
        label='Once-per-object' if limit is ActivationLimit.ONCE_PER_OBJECT else 'Exhaust'
        raw = annotations.get(field, ())
        if not isinstance(raw, (list, tuple)) or any(
            type(value) is not str or not value for value in raw
        ):
            raise ActivationUsageError(f"{label} activation usage is malformed")
        if len(raw) != len(set(raw)):
            raise ActivationUsageError(
                f"{label} activation usage contains duplicate abilities"
            )
        return (
            ActivationUsageVerdict(False, 'ability_already_activated_for_object' if limit is ActivationLimit.ONCE_PER_OBJECT else "exhaust_ability_already_activated")
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
    maximum: int = 1,
) -> None:
    """Commit one successful activation through the canonical usage owner."""

    verdict = activation_usage_verdict(
        source,
        ability_id=ability_id,
        limit=limit,
        turn_sequence=turn_sequence,
        maximum=maximum,
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
        if values.get(ability_id) == turn_sequence:
            annotations[_BOAST_SECOND_FIELD] = {**_second_boast_uses(annotations), ability_id: turn_sequence}
            return
        values[ability_id] = turn_sequence
        annotations[_ONCE_PER_TURN_FIELD] = values
        return
    field=_ONCE_PER_OBJECT_FIELD if limit is ActivationLimit.ONCE_PER_OBJECT else _EXHAUST_FIELD
    values = list(annotations.get(field, ()))
    values.append(ability_id)
    annotations[field] = sorted(values)


def reset_copied_activation_usage(source, *, ability_ids) -> None:
    """Discard marks for the newly acquired instances from a copied form."""
    selected = {_ability_id(identity) for identity in ability_ids}
    if not selected:
        return
    annotations = _annotations(source)
    updated = {}
    for field in (_ONCE_PER_TURN_FIELD, _BOAST_SECOND_FIELD, _ONCE_PER_OBJECT_FIELD, _EXHAUST_FIELD):
        if field not in annotations:
            continue
        stored = annotations[field]
        if field in {_ONCE_PER_TURN_FIELD, _BOAST_SECOND_FIELD}:
            if not isinstance(stored, Mapping):
                raise ActivationUsageError("Once-per-turn activation usage is malformed")
            updated[field] = {identity: turn for identity, turn in stored.items() if identity not in selected}
        else:
            if not isinstance(stored, (tuple, list)):
                raise ActivationUsageError("Once-per-object activation usage is malformed")
            updated[field] = [identity for identity in stored if identity not in selected]
    for field, retained in updated.items():
        if retained:
            source.annotations[field] = retained
        else:
            source.annotations.pop(field)


__all__ = [
    "ACTIVATION_ONCE_PER_TURN_CAPABILITY",
    "ACTIVATION_ONCE_PER_OBJECT_CAPABILITY",
    "ActivationLimit",
    "ActivationUsageError",
    "ActivationUsageVerdict",
    "activation_usage_verdict",
    "commit_activation_usage",
    "trigger_usage_available",
    "commit_trigger_usage",
    "cast_grant_usage_available",
    "commit_cast_grant_usage",
]
