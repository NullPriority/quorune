from __future__ import annotations

"""Typed current-turn history predicates for public targets."""

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


GRAVEYARD_BATTLEFIELD_ARRIVAL_FIELD = "graveyard_from_battlefield_this_turn"


def target_graveyard_battlefield_arrival_matches(*, history, turn_sequence, actor, card):
    """The current own graveyard card must retain its actual arrival this turn."""
    if (
        history is None or getattr(history, "arrival_history_version", None) != 1
        or card is None or card.zone != "graveyard" or card.owner != actor or not card.is_card_object
    ):
        return False
    return any(
        event.actor == actor and event.origin == "battlefield" and event.object_incarnation == card.logical_object_id
        for event in current_turn_history_events(history, turn_sequence=turn_sequence, kind="card_arrived_graveyard")
    )


PLAYER_HISTORY_FIELD = "player_history"


class TargetPlayerHistoryKind(str, Enum):
    LOST_LIFE = "lost_life_this_turn"
    DAMAGED_BY_SOURCE = "damaged_by_source_this_turn"


@dataclass(frozen=True, slots=True)
class TargetPlayerHistorySpec:
    """One exact fact about a player in the current turn."""

    kind: TargetPlayerHistoryKind

    def __post_init__(self) -> None:
        if not isinstance(self.kind, TargetPlayerHistoryKind):
            raise ValueError("Target player history requires a typed kind")

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TargetPlayerHistorySpec":
        if not isinstance(value, Mapping) or set(value) != {"kind"} or type(value["kind"]) is not str:
            raise ValueError("Target player history requires an exact kind")
        return cls(TargetPlayerHistoryKind(value["kind"]))


def target_player_history_matches(
    spec: TargetPlayerHistorySpec,
    *,
    history: TurnHistory | None,
    turn_sequence: int,
    player: str,
    source_incarnation: str | None,
) -> bool:
    """Match the positive public result and any required source incarnation."""
    if not isinstance(spec, TargetPlayerHistorySpec):
        raise ValueError("Target player history matching requires a typed spec")
    if spec.kind is TargetPlayerHistoryKind.LOST_LIFE:
        return any(event.target == player and event.amount > 0 for event in current_turn_history_events(
            history, turn_sequence=turn_sequence, kind="player_lost_life",
        ))
    if source_incarnation is None:
        return False
    return any(
        event.target == player and event.amount > 0 and event.object_incarnation == source_incarnation
        for event in current_turn_history_events(history, turn_sequence=turn_sequence, kind="player_damaged")
    )


__all__ = [
    "PLAYER_HISTORY_FIELD",
    "TargetPlayerHistoryKind",
    "TargetPlayerHistorySpec",
    "target_player_history_matches",
    "TargetDamageHistoryKind",
    "TargetDamageHistorySpec",
    "target_damage_history_matches",
]
