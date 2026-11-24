"""Typed additional beginning phases queued after the current postcombat main."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


ADDITIONAL_BEGINNING_FIELD = "additional_beginning_phases"


@dataclass(frozen=True, slots=True)
class AdditionalBeginningPhases:
    pending: int
    active: bool = False
    draws_completed: int = 0

    def __post_init__(self) -> None:
        if (
            type(self.pending) is not int or self.pending < 0
            or type(self.active) is not bool
            or type(self.draws_completed) is not int or self.draws_completed < 0
            or (not self.active and (not self.pending or self.draws_completed))
        ):
            raise ValueError("An additional beginning phase needs a live queue or active phase")

    def queue_one(self) -> AdditionalBeginningPhases:
        return AdditionalBeginningPhases(self.pending + 1, self.active, self.draws_completed)

    def begin_next(self) -> AdditionalBeginningPhases:
        if not self.pending:
            raise ValueError("The additional beginning phase queue is empty")
        return AdditionalBeginningPhases(self.pending - 1, active=True)

    def record_draw(self) -> AdditionalBeginningPhases:
        if not self.active:
            raise ValueError("The additional beginning phase is not active")
        return AdditionalBeginningPhases(self.pending, active=True, draws_completed=self.draws_completed + 1)

    def to_dict(self) -> dict[str, int | bool]:
        return {"pending": self.pending, "active": self.active, "draws_completed": self.draws_completed}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> AdditionalBeginningPhases:
        if not isinstance(value, Mapping) or set(value) != {"pending", "active", "draws_completed"}:
            raise ValueError("Malformed additional beginning phase state")
        return cls(value["pending"], value["active"], value["draws_completed"])


def decode_additional_beginning(data: Mapping[str, object]) -> AdditionalBeginningPhases | None:
    if ADDITIONAL_BEGINNING_FIELD not in data:
        return None
    return AdditionalBeginningPhases.from_dict(data[ADDITIONAL_BEGINNING_FIELD])


ADDITIONAL_BEGINNING_OPERATION = "schedule_additional_beginning"
ADDITIONAL_BEGINNING_CAPABILITY = "turn.phase.additional_beginning_after_postcombat"
ADDITIONAL_BEGINNING_CONTROLLER_VARIANT = "controller_postcombat_additional_beginning"
ADDITIONAL_BEGINNING_ENCHANTED_VARIANT = "enchanted_postcombat_additional_beginning"
ADDITIONAL_BEGINNING_VARIANTS = frozenset({
    ADDITIONAL_BEGINNING_CONTROLLER_VARIANT, ADDITIONAL_BEGINNING_ENCHANTED_VARIANT,
})


def additional_beginning_effect() -> dict[str, object]:
    return {"op": ADDITIONAL_BEGINNING_OPERATION, "player": "$context.player", "schema_version": 1}


def decode_additional_beginning_instruction(value, *, resolved=False) -> str:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"op", "player", "schema_version"}
        or value["op"] != ADDITIONAL_BEGINNING_OPERATION
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or type(value["player"]) is not str
        or not value["player"]
        or (not resolved and value["player"] != "$context.player")
    ):
        raise ValueError("Additional beginning needs the exact captured postcombat player")
    return value["player"]


def additional_beginning_condition(*, enchanted: bool = False):
    participant = (
        {"field": "source_attached_player", "op": "eq", "value": "$context.player"}
        if enchanted
        else {"field": "player", "op": "eq", "value": "$source.controller"}
    )
    return {"all": [
        participant,
        {"field": "step", "op": "eq", "value": "main"},
        {"field": "phase", "op": "eq", "value": "postcombat_main"},
    ]}


@dataclass(frozen=True, slots=True)
class ScheduleAdditionalBeginningIntent:
    actor: str
    player: str
    source_ref: str

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value for value in (self.actor, self.player, self.source_ref)):
            raise ValueError("Additional beginning requires a resolving actor, player and source")
