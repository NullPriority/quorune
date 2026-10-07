from __future__ import annotations

"""Immutable CR 502 untap-step participation values and planning."""

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .object_predicate import ObjectQuerySpec
from .object_query import ObjectQueryResult, object_matches_query


class UntapStepError(ValueError):
    """An untap-step descriptor or snapshot is malformed."""


class UntapInstruction(str, Enum):
    OPTIONAL = "optional"
    PROHIBIT = "prohibition"
    ADDITIONAL = "additional"
    LIMIT = "limit"


class UntapSubjectRelation(str, Enum):
    SOURCE = "source"
    ATTACHED_OBJECT = "attached_object"
    QUERY = "query"


class UntapTurnRelation(str, Enum):
    SUBJECT_CONTROLLER = "subject_controller"
    OTHER_PLAYER = "other_player"


@dataclass(frozen=True, slots=True)
class UntapStepParticipation:
    """One source-pinned rule that changes CR 502.3 participation."""

    participation_id: str
    source_object_id: str
    source_ref: str
    source_controller: str
    instruction: UntapInstruction
    subject_relation: UntapSubjectRelation
    turn_relation: UntapTurnRelation
    predicate: ObjectQuerySpec
    subject_object_id: str | None = None
    maximum: int | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "participation_id",
            "source_object_id",
            "source_ref",
            "source_controller",
        ):
            value = getattr(self, field_name)
            if type(value) is not str or not value:
                raise UntapStepError(
                    f"Untap-step {field_name} must be a nonempty string"
                )
        if not isinstance(self.instruction, UntapInstruction):
            raise UntapStepError("Untap-step instruction must be typed")
        if not isinstance(self.subject_relation, UntapSubjectRelation):
            raise UntapStepError("Untap-step subject relation must be typed")
        if not isinstance(self.turn_relation, UntapTurnRelation):
            raise UntapStepError("Untap-step turn relation must be typed")
        if not isinstance(self.predicate, ObjectQuerySpec):
            raise UntapStepError("Untap-step predicate must be typed")
        if self.subject_relation is UntapSubjectRelation.QUERY:
            if self.subject_object_id is not None:
                raise UntapStepError(
                    "Query untap-step subjects cannot pin one object"
                )
        elif type(self.subject_object_id) is not str or not self.subject_object_id:
            raise UntapStepError(
                "Source and attached untap-step subjects require one object"
            )
        if self.instruction is UntapInstruction.LIMIT:
            if type(self.maximum) is not int or self.maximum < 0:
                raise UntapStepError(
                    "Untap-step limits require a nonnegative integer maximum"
                )
        elif self.maximum is not None:
            raise UntapStepError(
                "Only untap-step limits may declare a maximum"
            )


@dataclass(frozen=True, slots=True)
class UntapStepPlan:
    active_player: str
    prohibited_object_ids: tuple[str, ...]
    additional_object_ids: tuple[str, ...]
    unsupported_source_object_id: str | None = None
    supporting_source_refs: tuple[str, ...] = ()
    optional_object_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.active_player) is not str or not self.active_player:
            raise UntapStepError(
                "An untap-step plan requires one active player"
            )
        for field_name in (
            "prohibited_object_ids",
            "additional_object_ids",
            "supporting_source_refs",
            "optional_object_ids",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)) or any(
                type(value) is not str or not value for value in values
            ):
                raise UntapStepError(
                    f"Untap-step {field_name} must contain unique strings"
                )
        overlap = set(self.prohibited_object_ids).intersection(
            self.additional_object_ids
        )
        if overlap:
            raise UntapStepError(
                "An object cannot be both prohibited and additionally untapped"
            )
        if self.unsupported_source_object_id is not None and (
            type(self.unsupported_source_object_id) is not str
            or not self.unsupported_source_object_id
        ):
            raise UntapStepError(
                "Unsupported untap-step source identity must be nonempty"
            )


def _matching_subjects(
    participation: UntapStepParticipation,
    rows: tuple[ObjectQueryResult, ...],
) -> tuple[ObjectQueryResult, ...]:
    return tuple(
        row
        for row in rows
        if (
            participation.subject_relation is UntapSubjectRelation.QUERY
            or row.object_id == participation.subject_object_id
        )
        and object_matches_query(row, participation.predicate)
    )


def _turn_matches(
    participation: UntapStepParticipation,
    row: ObjectQueryResult,
    active_player: str,
) -> bool:
    if participation.turn_relation is UntapTurnRelation.SUBJECT_CONTROLLER:
        return row.controller == active_player
    return (
        active_player != participation.source_controller
        and row.controller == participation.source_controller
    )


def plan_untap_step(
    active_player: str,
    rows: Iterable[ObjectQueryResult],
    participations: Iterable[UntapStepParticipation],
) -> UntapStepPlan:
    """Resolve one deterministic, mutation-free untap participation plan."""

    if type(active_player) is not str or not active_player:
        raise UntapStepError("Untap-step active player must be nonempty")
    snapshot = tuple(rows)
    object_ids = [row.object_id for row in snapshot]
    if len(object_ids) != len(set(object_ids)) or any(
        type(value) is not str or not value for value in object_ids
    ):
        raise UntapStepError(
            "Untap-step snapshot object identities must be unique"
        )
    normalized = tuple(
        sorted(participations, key=lambda value: value.participation_id)
    )
    participation_ids = [value.participation_id for value in normalized]
    if len(participation_ids) != len(set(participation_ids)):
        raise UntapStepError(
            "Untap-step participation identities must be unique"
        )

    prohibited: set[str] = set()
    additional: set[str] = set()
    optional: set[str] = set()
    supporting_refs: set[str] = set()
    unsupported_source: str | None = None
    for participation in normalized:
        matches = tuple(
            row
            for row in _matching_subjects(participation, snapshot)
            if _turn_matches(participation, row, active_player)
        )
        if not matches:
            continue
        supporting_refs.add(participation.source_ref)
        if participation.instruction is UntapInstruction.LIMIT:
            unsupported_source = participation.source_object_id
            break
        if participation.instruction is UntapInstruction.PROHIBIT:
            prohibited.update(row.object_id for row in matches)
        elif participation.instruction is UntapInstruction.OPTIONAL:
            optional.update(row.object_id for row in matches if row.controller == active_player and row.tapped)
        else:
            additional.update(row.object_id for row in matches)

    # A prohibition applicable in the current step wins over an additional
    # instruction for the same physical object.  The closed compiler family
    # normally makes the two turn relations disjoint, but this keeps composed
    # reviewed descriptors deterministic and fail-safe.
    additional.difference_update(prohibited)
    optional.difference_update(prohibited)
    return UntapStepPlan(
        active_player=active_player,
        prohibited_object_ids=tuple(sorted(prohibited)),
        additional_object_ids=tuple(sorted(additional)),
        unsupported_source_object_id=unsupported_source,
        supporting_source_refs=tuple(sorted(supporting_refs)),
        optional_object_ids=tuple(sorted(optional)),
    )


__all__ = [
    "plan_untap_step",
    "UntapInstruction",
    "UntapStepError",
    "UntapStepParticipation",
    "UntapStepPlan",
    "UntapSubjectRelation",
    "UntapTurnRelation",
]
