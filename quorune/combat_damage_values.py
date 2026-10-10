from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class CombatDamageAssignmentError(ValueError):
    """A combat-damage proposal or assignment value is malformed."""


def assignment_identity(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CombatDamageAssignmentError(f"{label} must be a nonempty string")
    return value


def exact_assignment_integer(
    value: object,
    *,
    label: str,
    minimum: int = 0,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CombatDamageAssignmentError(f"{label} must be an exact integer")
    if value < minimum:
        raise CombatDamageAssignmentError(
            f"{label} cannot be less than {minimum}"
        )
    return value


@dataclass(frozen=True, slots=True)
class DamageAssignment:
    source: str
    target: str
    amount: int

    def __post_init__(self) -> None:
        assignment_identity(self.source, label="Damage source")
        assignment_identity(self.target, label="Damage recipient")
        if type(self.amount) is int and self.amount < 0:
            raise CombatDamageAssignmentError("Damage cannot be negative")
        exact_assignment_integer(self.amount, label="Damage amount")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "amount": self.amount,
        }


@dataclass(frozen=True, slots=True)
class CreatureDamageState:
    toughness: int
    marked_damage: int

    def __post_init__(self) -> None:
        if not isinstance(self.toughness, int) or isinstance(
            self.toughness, bool
        ):
            raise CombatDamageAssignmentError(
                "Blocker toughness must be an exact integer"
            )
        exact_assignment_integer(
            self.marked_damage,
            label="Blocker marked damage",
        )


@dataclass(frozen=True, slots=True)
class TrampleDamageSpec:
    attacker: str
    spill_target: str
    blockers: tuple[tuple[str, CreatureDamageState], ...]

    def __post_init__(self) -> None:
        assignment_identity(self.attacker, label="Trample source")
        assignment_identity(self.spill_target, label="Trample spill target")
        if isinstance(self.blockers, (str, bytes)):
            raise CombatDamageAssignmentError(
                "Trample blockers must be typed reference-state pairs"
            )
        blockers = tuple(self.blockers)
        object.__setattr__(self, "blockers", blockers)
        if not all(
            isinstance(value, tuple) and len(value) == 2
            for value in blockers
        ):
            raise CombatDamageAssignmentError(
                "Trample blockers must be typed reference-state pairs"
            )
        blocker_refs = [reference for reference, _state in blockers]
        if len(blocker_refs) != len(set(blocker_refs)) or not all(blocker_refs):
            raise CombatDamageAssignmentError(
                "Trample blockers must have unique nonempty references"
            )
        if not all(
            isinstance(state, CreatureDamageState) for _, state in blockers
        ):
            raise CombatDamageAssignmentError(
                "Trample blockers require typed damage states"
            )


@dataclass(frozen=True, slots=True)
class AsUnblockedDamageSpec:
    attacker: str
    recipient: str

    def __post_init__(self):
        assignment_identity(self.attacker,label='As-unblocked attacker')
        assignment_identity(self.recipient,label='As-unblocked recipient')
        if self.attacker==self.recipient:raise CombatDamageAssignmentError('An attacker cannot assign damage to itself as unblocked')


__all__ = [
    "assignment_identity",
    "CombatDamageAssignmentError",
    "CreatureDamageState",
    "DamageAssignment",
    "exact_assignment_integer",
    "TrampleDamageSpec",
]
