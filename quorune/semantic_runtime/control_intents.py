from __future__ import annotations

"""Immutable requests to the shared resolution-control owner."""

from dataclasses import dataclass

from ..continuous_effect_model import ContinuousEffectDuration
from ..continuous_effect_state import ResolutionEffectSource
from ..object_predicate import ObjectQuerySpec
from ..source_continuity import SourceContinuitySnapshot


def _validate_control(actor, controller, duration, source, reason):
    if any(type(value) is not str or not value for value in (actor, controller, reason)):
        raise ValueError("Control intents require an actor, controller and reason")
    if actor != controller:
        raise ValueError("This control family is acquired by the resolving controller")
    if not isinstance(duration, ContinuousEffectDuration) or not (
        duration.source_bound or duration in {duration.UNTIL_END_OF_TURN, duration.ZONE_OBJECT}
    ):
        raise ValueError("Control intent duration is unsupported")
    if not isinstance(source, ResolutionEffectSource):
        raise ValueError("Control intents require a typed original source")


@dataclass(frozen=True, slots=True)
class GainControlIntent:
    actor: str
    object_ref: str
    controller: str
    duration: ContinuousEffectDuration
    source: ResolutionEffectSource
    reason: str
    history_snapshot: SourceContinuitySnapshot | None = None
    resolution_timestamp: int | None = None

    def __post_init__(self) -> None:
        _validate_control(self.actor, self.controller, self.duration, self.source, self.reason)
        if type(self.object_ref) is not str or not self.object_ref:
            raise ValueError("Control intents require one public permanent reference")
        if self.history_snapshot is not None and not isinstance(self.history_snapshot, SourceContinuitySnapshot):
            raise ValueError("Control duration history must be a typed retained snapshot")
        if self.resolution_timestamp is not None and (
            type(self.resolution_timestamp) is not int or self.resolution_timestamp < 0
        ):
            raise ValueError("Control resolution timestamp must be a nonnegative integer")


@dataclass(frozen=True, slots=True)
class GainControlSetIntent:
    actor: str
    predicate: ObjectQuerySpec
    controller: str
    duration: ContinuousEffectDuration
    source: ResolutionEffectSource
    steps: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        _validate_control(self.actor, self.controller, self.duration, self.source, self.reason)
        if not isinstance(self.predicate, ObjectQuerySpec):
            raise ValueError("Control-set intents require a typed public query")
        if self.duration.source_bound:
            raise ValueError("This control-set family cannot add a source duration")
        allowed = {
            ("gain_control",), ("untap", "gain_control"), ("gain_control", "untap"),
            ("gain_control", "haste"), ("untap", "gain_control", "haste"),
            ("gain_control", "untap", "haste"),
        }
        if type(self.steps) is not tuple or self.steps not in allowed:
            raise ValueError("Control-set instruction order is unsupported")
        if "haste" in self.steps and self.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN:
            raise ValueError("Control-set haste requires its printed end-of-turn duration")


ControlIntent = GainControlIntent | GainControlSetIntent
