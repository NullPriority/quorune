"""Additional combat phases and their recurring turn-bounded untap result."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum


SCHEDULE_FIELD = "main_phase_combat_schedule"
MAIN_COMBAT_FIELD = "fixed_combats_after_main"
ATTACK_UNTAP_FIELD = "untap_turn_attackers"
MAIN_COMBAT_CAPABILITY = "turn.phase.fixed_combats_after_main"
ATTACK_UNTAP_CAPABILITY = "trigger.delayed.untap_turn_attackers"
MAIN_COMBAT_MECHANIC = "fixed-combats-after-main-with-attacker-untap"
IMMEDIATE_COMBAT_CAPABILITY = "turn.phase.combat_and_main_after_attacker_untap"
IMMEDIATE_COMBAT_MECHANIC = "fixed-combat-and-main-after-attacker-untap"
MAIN_PHASES = frozenset({"precombat_main", "postcombat_main"})
INSERTED_MAIN_COUNT_FIELD = "inserted_main_phase_count"
LATER_MAIN_PHASE_FIELD = "later_than_second_main_phase"
POSTCOMBAT_MAIN = "postcombat_main"
_EXTRA_COMBAT = "combat"
_INSERTED_PHASES = frozenset({_EXTRA_COMBAT, "beginning", POSTCOMBAT_MAIN})


class MainPhaseCombatInstruction(Enum):
    TWO_COMBATS_DELAYED_UNTAP = 1
    COMBAT_THEN_MAIN_IMMEDIATE_UNTAP = 2

    @property
    def phases(self) -> tuple[str, ...]:
        if self is self.COMBAT_THEN_MAIN_IMMEDIATE_UNTAP:
            return (_EXTRA_COMBAT, POSTCOMBAT_MAIN)
        return (_EXTRA_COMBAT, _EXTRA_COMBAT)


@dataclass(frozen=True, slots=True)
class MainPhaseCombatSchedule:
    """Preserve an insertion boundary while its ordinary phases are suspended."""

    main_phase: str
    pending: tuple[str, ...]
    active_phase: str | None = None
    reached_inserted_main: bool = False

    def __post_init__(self) -> None:
        if (type(self.main_phase) is not str or self.main_phase not in MAIN_PHASES
                or type(self.pending) is not tuple
                or any(type(phase) is not str or phase not in _INSERTED_PHASES for phase in self.pending)
                or self.active_phase is not None and (type(self.active_phase) is not str or self.active_phase not in _INSERTED_PHASES)
                or not self.pending and self.active_phase is None
                or type(self.reached_inserted_main) is not bool):
            raise ValueError("An inserted combat requires its original main-phase boundary and queue")
        if self.main_phase == "precombat_main" and not self.reached_inserted_main and (
            "beginning" in (*self.pending, self.active_phase) or self.active_phase == POSTCOMBAT_MAIN
        ):
            raise ValueError("An inserted beginning phase needs a reached postcombat main phase")
        if self.reached_inserted_main and (self.main_phase == POSTCOMBAT_MAIN or self.active_phase is None):
            raise ValueError("A reached inserted main requires an active insertion after precombat main")
        for previous, current in zip((self.active_phase, *self.pending), self.pending):
            if current == POSTCOMBAT_MAIN and previous != _EXTRA_COMBAT:
                raise ValueError("An inserted main must follow its inserted combat")

    def prepend(self, *phases: str) -> MainPhaseCombatSchedule:
        if self.active_phase not in (None, POSTCOMBAT_MAIN):
            raise ValueError("New phases can only be inserted after the current main phase")
        return replace(self, pending=(*phases, *self.pending))

    def begin_next(self) -> MainPhaseCombatSchedule:
        if not self.pending:
            raise ValueError("The main phase has no remaining inserted phase")
        phase = self.pending[0]
        reached_main = self.reached_inserted_main or (self.main_phase == "precombat_main" and phase == POSTCOMBAT_MAIN)
        return replace(self, pending=self.pending[1:], active_phase=phase, reached_inserted_main=reached_main)

    def to_dict(self) -> dict[str, object]:
        return {
            "main_phase": self.main_phase, "pending": list(self.pending), "active_phase": self.active_phase,
            **({"reached_inserted_main": True} if self.reached_inserted_main else {}),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> MainPhaseCombatSchedule:
        if not isinstance(value, Mapping):
            raise ValueError("Malformed main-phase combat schedule")
        reached = "reached_inserted_main" in value
        fields = {"main_phase", "pending", "active_phase"} | ({"reached_inserted_main"} if reached else set())
        if (set(value) != fields or not isinstance(value["pending"], (list, tuple))
                or reached and value["reached_inserted_main"] is not True):
            raise ValueError("Malformed main-phase combat schedule")
        return cls(value["main_phase"], tuple(value["pending"]), value["active_phase"], reached)


def decode_main_phase_combat_schedule(data: Mapping[str, object]) -> MainPhaseCombatSchedule | None:
    return MainPhaseCombatSchedule.from_dict(data[SCHEDULE_FIELD]) if SCHEDULE_FIELD in data else None


def decode_inserted_main_phase_count(data: Mapping[str, object]) -> int:
    if INSERTED_MAIN_COUNT_FIELD not in data:
        return 0
    count = data[INSERTED_MAIN_COUNT_FIELD]
    if type(count) is not int or count < 1:
        raise ValueError("The turn's inserted main phase count must be a positive integer")
    return count


def later_main_phase_context(*, phase, step, schedule, inserted_count) -> dict[str, bool]:
    if (phase, step) != (POSTCOMBAT_MAIN, "main"):
        return {}
    inserted_after_first = (schedule is not None and schedule.main_phase == "precombat_main"
                            and schedule.active_phase == POSTCOMBAT_MAIN)
    return {LATER_MAIN_PHASE_FIELD: True} if inserted_count > int(inserted_after_first) else {}


def main_phase_combat_effect(instruction=MainPhaseCombatInstruction.TWO_COMBATS_DELAYED_UNTAP) -> dict[str, object]:
    return {"op": "delayed_trigger", MAIN_COMBAT_FIELD: instruction.value}


def main_phase_combat_instruction(effect: Mapping[str, object]) -> MainPhaseCombatInstruction:
    if (not isinstance(effect, Mapping) or set(effect) != {"op", MAIN_COMBAT_FIELD}
            or effect["op"] != "delayed_trigger" or type(effect[MAIN_COMBAT_FIELD]) is not int):
        raise ValueError("Additional combat phases require their closed spell instruction")
    return MainPhaseCombatInstruction(effect[MAIN_COMBAT_FIELD])


def attack_untap_effect() -> dict[str, object]:
    return {"op": "set_public_tap_state", ATTACK_UNTAP_FIELD: 1}


def validate_combat_instruction(effect: Mapping[str, object], *, delayed_result: bool = False) -> None:
    if not delayed_result:
        main_phase_combat_instruction(effect)
        return
    if (not isinstance(effect, Mapping) or type(effect.get(ATTACK_UNTAP_FIELD)) is not int
            or dict(effect) != attack_untap_effect()):
        raise ValueError("Delayed attacker untap requires its closed instruction")


@dataclass(frozen=True, slots=True)
class UntapTurnAttackersIntent:
    actor: str
    stack_ref: str
    reason: str

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value for value in (self.actor, self.stack_ref, self.reason)):
            raise ValueError("Turn-attack untap requires its controller and resolving delayed stack object")
