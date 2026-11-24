"""Typed follow-ups of an ordinary simultaneous battlefield exile."""

from dataclasses import dataclass

from ..rules.targeted_exile_token_results import TargetedExileTokenFollowup
from ..zone_trigger_events import ZoneTransitionKind
from .intents import MoveObjectsSimultaneouslyIntent


@dataclass(frozen=True, slots=True)
class CommittedTargetExileIntent(MoveObjectsSimultaneouslyIntent):
    def __post_init__(self) -> None:
        MoveObjectsSimultaneouslyIntent.__post_init__(self)
        if (
            self.expected_zones != ("battlefield",) or self.destination != "exile"
            or self.transition_kind is not ZoneTransitionKind.ORDINARY
            or self.owned_only or self.controlled_only
        ):
            raise ValueError("Targeted exile results require an ordinary public battlefield move")


@dataclass(frozen=True, slots=True)
class TargetedExileTokensIntent(CommittedTargetExileIntent):
    followup: TargetedExileTokenFollowup | None = None

    def __post_init__(self) -> None:
        CommittedTargetExileIntent.__post_init__(self)
        if not isinstance(self.followup, TargetedExileTokenFollowup):
            raise ValueError("Targeted exile tokens require a typed fixed token")


@dataclass(frozen=True, slots=True)
class OpponentExileDrawIntent(CommittedTargetExileIntent):
    """Draw for each distinct opponent whose selected permanent reaches exile."""
