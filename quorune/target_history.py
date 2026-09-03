from __future__ import annotations

"""Typed current-turn history predicates for public permanent targets."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .model import TurnHistory
from .turn_history import current_turn_history_events


class TargetDamageHistoryKind(str, Enum):
    WAS_DEALT_DAMAGE = "was_dealt_damage"
    DEALT_DAMAGE = "dealt_damage"
    DEALT_DAMAGE_TO_ACTOR = "dealt_damage_to_actor"


@dataclass(frozen=True, slots=True)
class TargetDamageHistorySpec:
    """One positive-damage fact about the current logical incarnation."""

    kind: TargetDamageHistoryKind

    def __post_init__(self) -> None:
        if not isinstance(self.kind, TargetDamageHistoryKind):
            raise ValueError("Target damage-history kind is unsupported")

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value}

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "TargetDamageHistorySpec":
        if not isinstance(value, Mapping) or set(value) != {"kind"}:
            raise ValueError(
                "Target damage-history predicates require an exact kind"
            )
        try:
            return cls(TargetDamageHistoryKind(value["kind"]))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Target damage-history kind is unsupported"
            ) from exc


def target_damage_history_matches(
    spec: TargetDamageHistorySpec,
    *,
    history: TurnHistory | None,
    turn_sequence: int,
    actor: str,
    object_incarnation: str,
) -> bool:
    """Evaluate one target history predicate against typed positive results."""

    if not isinstance(spec, TargetDamageHistorySpec):
        raise ValueError("Target damage-history matching requires a typed spec")
    if spec.kind is TargetDamageHistoryKind.WAS_DEALT_DAMAGE:
        return any(
            event.target_object_incarnation == object_incarnation
            and event.amount > 0
            for event in current_turn_history_events(
                history,
                turn_sequence=turn_sequence,
                kind="permanent_damaged",
            )
        )
    events = (
        *current_turn_history_events(
            history,
            turn_sequence=turn_sequence,
            kind="player_damaged",
        ),
        *current_turn_history_events(
            history,
            turn_sequence=turn_sequence,
            kind="permanent_damaged",
        ),
    )
    return any(
        event.object_incarnation == object_incarnation
        and event.amount > 0
        and (
            spec.kind is TargetDamageHistoryKind.DEALT_DAMAGE
            or (
                event.kind == "player_damaged"
                and event.target == actor
            )
        )
        for event in events
    )


__all__ = [
    "TargetDamageHistoryKind",
    "TargetDamageHistorySpec",
    "target_damage_history_matches",
]
