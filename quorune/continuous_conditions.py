from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from .counter_names import CounterStateError, normalized_counter_name


class FixedPublicStateConditionError(ValueError):
    """A fixed public-state continuous-effect condition is malformed."""


FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID = (
    "continuous.characteristics.fixed-public-state.v1"
)


class FixedPublicStateConditionKind(StrEnum):
    CONTROLLER_TURN = "controller_turn"
    OTHER_TURN = "other_turn"
    CONTROLLER_GRAVEYARD_CARD_COUNT_AT_LEAST = (
        "controller_graveyard_card_count_at_least"
    )
    CONTROLLER_HAND_COUNT_AT_MOST = "controller_hand_count_at_most"
    CONTROLLER_LIFE_AT_LEAST = "controller_life_at_least"
    CONTROLLER_LIFE_AT_MOST = "controller_life_at_most"
    OPPONENT_LIFE_AT_MOST = "opponent_life_at_most"
    SOURCE_ENTERED_THIS_TURN = "source_entered_this_turn"
    SOURCE_COUNTER_AT_LEAST = "source_counter_at_least"


@dataclass(frozen=True, slots=True)
class FixedPublicStateConditionSnapshot:
    """Authoritative non-characteristic facts for one static source."""

    source_controller: str
    active_player: str | None
    controller_hand_count: int
    controller_graveyard_card_count: int
    controller_life: int
    opponent_life_totals: tuple[int, ...]
    turn_sequence: int
    source_entered_battlefield_turn_sequence: int
    source_counters: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if type(self.source_controller) is not str or not self.source_controller:
            raise FixedPublicStateConditionError(
                "A public-state condition requires a source controller"
            )
        if self.active_player is not None and (
            type(self.active_player) is not str or not self.active_player
        ):
            raise FixedPublicStateConditionError(
                "The active player must be a nonempty string or null"
            )
        for field_name in (
            "controller_hand_count",
            "controller_graveyard_card_count",
            "turn_sequence",
            "source_entered_battlefield_turn_sequence",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise FixedPublicStateConditionError(
                    f"{field_name} must be a nonnegative integer"
                )
        if type(self.controller_life) is not int:
            raise FixedPublicStateConditionError(
                "Controller life must be an integer"
            )
        if any(type(value) is not int for value in self.opponent_life_totals):
            raise FixedPublicStateConditionError(
                "Opponent life totals must be integers"
            )
        normalized: list[tuple[str, int]] = []
        for raw_name, amount in self.source_counters:
            if type(raw_name) is not str:
                raise FixedPublicStateConditionError(
                    "Source counter names must be strings"
                )
            try:
                name = normalized_counter_name(raw_name)
            except CounterStateError as exc:
                raise FixedPublicStateConditionError(str(exc)) from exc
            if type(amount) is not int or amount < 0:
                raise FixedPublicStateConditionError(
                    "Source counter amounts must be nonnegative integers"
                )
            normalized.append((name, amount))
        canonical = tuple(sorted(normalized))
        if len({name for name, _amount in canonical}) != len(canonical):
            raise FixedPublicStateConditionError(
                "Source counter names must be unique"
            )
        object.__setattr__(self, "source_counters", canonical)


@dataclass(frozen=True, slots=True)
class FixedPublicStateConditionSpec:
    """One closed condition for a fixed static characteristic effect."""

    kind: FixedPublicStateConditionKind
    amount: int | None = None
    counter_name: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise FixedPublicStateConditionError(
                "Unsupported fixed public-state condition schema version"
            )
        if not isinstance(self.kind, FixedPublicStateConditionKind):
            raise FixedPublicStateConditionError(
                "Unsupported fixed public-state condition kind"
            )
        amount_kinds = {
            FixedPublicStateConditionKind.CONTROLLER_GRAVEYARD_CARD_COUNT_AT_LEAST,
            FixedPublicStateConditionKind.CONTROLLER_HAND_COUNT_AT_MOST,
            FixedPublicStateConditionKind.CONTROLLER_LIFE_AT_LEAST,
            FixedPublicStateConditionKind.CONTROLLER_LIFE_AT_MOST,
            FixedPublicStateConditionKind.OPPONENT_LIFE_AT_MOST,
            FixedPublicStateConditionKind.SOURCE_COUNTER_AT_LEAST,
        }
        if self.kind in amount_kinds:
            if type(self.amount) is not int or self.amount < 0:
                raise FixedPublicStateConditionError(
                    "Fixed public-state thresholds must be nonnegative integers"
                )
        elif self.amount is not None:
            raise FixedPublicStateConditionError(
                "This fixed public-state condition cannot carry an amount"
            )
        if self.kind is FixedPublicStateConditionKind.SOURCE_COUNTER_AT_LEAST:
            if type(self.counter_name) is not str:
                raise FixedPublicStateConditionError(
                    "A source-counter condition requires a counter name"
                )
            try:
                canonical = normalized_counter_name(self.counter_name)
            except CounterStateError as exc:
                raise FixedPublicStateConditionError(str(exc)) from exc
            object.__setattr__(self, "counter_name", canonical)
            if self.amount == 0:
                raise FixedPublicStateConditionError(
                    "A source-counter condition requires a positive amount"
                )
        elif self.counter_name is not None:
            raise FixedPublicStateConditionError(
                "Only source-counter conditions may carry a counter name"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "amount": self.amount,
            "counter_name": self.counter_name,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "FixedPublicStateConditionSpec":
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "kind",
            "amount",
            "counter_name",
        }:
            raise FixedPublicStateConditionError(
                "Fixed public-state conditions have a closed schema"
            )
        try:
            kind = FixedPublicStateConditionKind(value["kind"])
        except (TypeError, ValueError) as exc:
            raise FixedPublicStateConditionError(
                "Unsupported fixed public-state condition kind"
            ) from exc
        return cls(
            schema_version=value["schema_version"],
            kind=kind,
            amount=value["amount"],
            counter_name=value["counter_name"],
        )

    def matches(self, snapshot: FixedPublicStateConditionSnapshot) -> bool:
        if not isinstance(snapshot, FixedPublicStateConditionSnapshot):
            raise FixedPublicStateConditionError(
                "Fixed public-state matching requires a typed snapshot"
            )
        if self.kind is FixedPublicStateConditionKind.CONTROLLER_TURN:
            return snapshot.active_player == snapshot.source_controller
        if self.kind is FixedPublicStateConditionKind.OTHER_TURN:
            return (
                snapshot.active_player is not None
                and snapshot.active_player != snapshot.source_controller
            )
        assert self.amount is not None or self.kind in {
            FixedPublicStateConditionKind.SOURCE_ENTERED_THIS_TURN,
        }
        if (
            self.kind
            is FixedPublicStateConditionKind.CONTROLLER_GRAVEYARD_CARD_COUNT_AT_LEAST
        ):
            return snapshot.controller_graveyard_card_count >= int(self.amount)
        if self.kind is FixedPublicStateConditionKind.CONTROLLER_HAND_COUNT_AT_MOST:
            return snapshot.controller_hand_count <= int(self.amount)
        if self.kind is FixedPublicStateConditionKind.CONTROLLER_LIFE_AT_LEAST:
            return snapshot.controller_life >= int(self.amount)
        if self.kind is FixedPublicStateConditionKind.CONTROLLER_LIFE_AT_MOST:
            return snapshot.controller_life <= int(self.amount)
        if self.kind is FixedPublicStateConditionKind.OPPONENT_LIFE_AT_MOST:
            return any(
                life <= int(self.amount)
                for life in snapshot.opponent_life_totals
            )
        if self.kind is FixedPublicStateConditionKind.SOURCE_ENTERED_THIS_TURN:
            return (
                snapshot.turn_sequence > 0
                and snapshot.source_entered_battlefield_turn_sequence
                == snapshot.turn_sequence
            )
        if self.kind is FixedPublicStateConditionKind.SOURCE_COUNTER_AT_LEAST:
            counters = dict(snapshot.source_counters)
            return counters.get(str(self.counter_name), 0) >= int(self.amount)
        raise FixedPublicStateConditionError(
            "Unsupported fixed public-state condition kind"
        )


__all__ = [
    "FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID",
    "FixedPublicStateConditionError",
    "FixedPublicStateConditionKind",
    "FixedPublicStateConditionSnapshot",
    "FixedPublicStateConditionSpec",
]
