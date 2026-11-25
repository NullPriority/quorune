from __future__ import annotations

"""One sealed face-up replacement result for a retained permanent incarnation."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any


FACE_UP_COPY_EVENT = "permanent.face_up.copy"
FACE_UP_COPY_RESULT = "copy_result"


@dataclass(frozen=True, slots=True)
class FaceUpCopySubject:
    """One proposed face-up transition of an exact battlefield incarnation."""

    reference: str
    incarnation: str
    controller: str
    timestamp: int

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value.strip() or value.strip() != value
               for value in (self.reference, self.incarnation, self.controller)):
            raise ValueError("A face-up copy requires one public reference, incarnation and controller")
        if type(self.timestamp) is not int or self.timestamp < 0:
            raise ValueError("A face-up copy requires the current status timestamp")

    @property
    def occurrence(self) -> str:
        return f"face-up-copy:{self.reference}:{self.timestamp}"

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "reference": self.reference, "incarnation": self.incarnation,
                "controller": self.controller, "timestamp": self.timestamp}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FaceUpCopySubject":
        fields = {"schema_version", "reference", "incarnation", "controller", "timestamp"}
        if not isinstance(raw, Mapping) or set(raw) != fields or type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
            raise ValueError("A face-up copy subject has a closed version-one shape")
        return cls(raw["reference"], raw["incarnation"], raw["controller"], raw["timestamp"])


@dataclass(frozen=True, slots=True)
class FaceUpCopyResult:
    """Keep a chosen permanent and its sealed copiable values together."""

    target_ref: str | None = None
    target_incarnation: str | None = None
    values: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.target_ref is None:
            if self.target_incarnation is not None or self.values is not None:
                raise ValueError("Declining a face-up copy carries no source or values")
            return
        if any(type(value) is not str or not value.strip() or value.strip() != value
               for value in (self.target_ref, self.target_incarnation)):
            raise ValueError("A face-up copy must bind the selected permanent incarnation")
        from ..entry_copy import COPIABLE_FACE_FIELD, COPIABLE_ORACLE_FIELD

        if (not isinstance(self.values, Mapping)
                or any(type(self.values.get(key)) is not str for key in ("name", "type_line", COPIABLE_ORACLE_FIELD, COPIABLE_FACE_FIELD))
                or not all(self.values[key] for key in ("type_line", COPIABLE_ORACLE_FIELD, COPIABLE_FACE_FIELD))):
            raise ValueError("A face-up copy requires detached copiable characteristics and provenance")
        from .immutable import FrozenMap

        object.__setattr__(self, "values", FrozenMap(self.values))

    def to_dict(self) -> dict[str, Any]:
        from .immutable import thaw_value

        return {"target_ref": self.target_ref, "target_incarnation": self.target_incarnation,
                "values": thaw_value(self.values) if self.values is not None else None}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FaceUpCopyResult":
        if not isinstance(raw, Mapping) or set(raw) != {"target_ref", "target_incarnation", "values"}:
            raise ValueError("A face-up copy result has a closed shape")
        return cls(raw["target_ref"], raw["target_incarnation"], raw["values"])


def validate_face_up_copy_event(event) -> None:
    result = event.payload.get(FACE_UP_COPY_RESULT)
    fields = {"subject", FACE_UP_COPY_RESULT}
    if result is not None:
        fields.add("replacement_choosers")
    if set(event.payload) != fields or event.children or event.entry_scope is not None:
        raise ValueError("A face-up copy event carries one subject and one choice result")
    subject = FaceUpCopySubject.from_dict(event.payload["subject"])
    if (event.affected_player != subject.controller or event.event_id != subject.occurrence
            or len(event.applied_effects) != int(result is not None)):
        raise ValueError("The face-up copy event changed its chooser, identity or application")
    if result is not None:
        selected = FaceUpCopyResult.from_dict(result)
        occurrence, = event.applied_effects
        if (not occurrence.startswith(subject.occurrence + ":")
                or event.payload["replacement_choosers"] != {occurrence: subject.controller}
                or selected.target_ref is not None and occurrence != f"{subject.occurrence}:{selected.target_ref}"):
            raise ValueError("The applied face-up copy must match its chooser journal and result")
        if selected.target_ref == subject.reference or selected.target_incarnation == subject.incarnation:
            raise ValueError("A face-up permanent can copy another object")


def validate_face_up_copy_replacement(effect, operations, decline_operations) -> None:
    from .model import ReplacementClass
    from .operations import SetField

    guard = effect.conditions.get("subject")
    if not isinstance(guard, Mapping) or set(guard) != {"eq"}:
        raise ValueError("A face-up copy replacement must bind the exact proposed subject")
    subject = FaceUpCopySubject.from_dict(guard["eq"])
    if (dict(effect.conditions) != _face_up_copy_conditions(subject)
            or effect.source_id != subject.reference or effect.occurrence_id != subject.occurrence
            or effect.replacement_class is not ReplacementClass.SELF_REPLACEMENT or not effect.optional
            or len(operations) != 1 or len(decline_operations) != 1
            or any(not isinstance(op, SetField) or op.field != FACE_UP_COPY_RESULT for op in (*operations, *decline_operations))):
        raise ValueError("A face-up copy replacement has one shared optional occurrence")
    result = FaceUpCopyResult.from_dict(operations[0].value)
    declined = FaceUpCopyResult.from_dict(decline_operations[0].value)
    if (result.target_ref is None or result.target_ref == subject.reference
            or result.target_incarnation == subject.incarnation or declined.target_ref is not None
            or effect.effect_id != f"{subject.occurrence}:{result.target_ref}"):
        raise ValueError("A face-up copy alternative must bind one distinct, selected permanent")


def _face_up_copy_conditions(subject: FaceUpCopySubject) -> dict[str, Any]:
    return {"subject": {"eq": subject.to_dict()}, FACE_UP_COPY_RESULT: {"eq": None}}


def face_up_copy_batch(subject: FaceUpCopySubject, candidates: tuple[FaceUpCopyResult, ...], *, apnap_order: tuple[str, ...]):
    """Seal the public alternatives and their private result values for one face-up attempt."""
    from .model import ReplaceableEvent, ReplacementClass, ReplacementEffect, ReplacementEventBatch
    from .operations import SetField

    if not isinstance(subject, FaceUpCopySubject) or any(not isinstance(row, FaceUpCopyResult) for row in candidates):
        raise ValueError("A face-up copy batch requires typed subject and candidates")
    if len({row.target_ref for row in candidates}) != len(candidates):
        raise ValueError("A face-up copy batch requires distinct public candidates")
    event = ReplaceableEvent(subject.occurrence, FACE_UP_COPY_EVENT, subject.controller,
                            {"subject": subject.to_dict(), FACE_UP_COPY_RESULT: None})
    effects = tuple(ReplacementEffect(
        effect_id=f"{subject.occurrence}:{row.target_ref}", source_id=subject.reference,
        event_kind=FACE_UP_COPY_EVENT, replacement_class=ReplacementClass.SELF_REPLACEMENT,
        conditions=_face_up_copy_conditions(subject),
        operations=(SetField(FACE_UP_COPY_RESULT, row.to_dict()),),
        decline_operations=(SetField(FACE_UP_COPY_RESULT, FaceUpCopyResult().to_dict()),),
        optional=True, label=f"Copy {row.target_ref}", decline_label="Keep original values",
        occurrence_id=subject.occurrence,
    ) for row in candidates)
    return ReplacementEventBatch(subject.occurrence, (event,), apnap_order), effects


def face_up_copy_subject(card) -> FaceUpCopySubject:
    return FaceUpCopySubject(card.ref, card.logical_object_id, card.controller, card.zone_timestamp)


def face_up_copy_payment_scope(batch, effects, *, action, seat, response):
    """Validate an unresolved copy choice reached by the paid face-up action."""
    from ..mana_mode_effects import mana_damage_event_belongs_to_payment

    if action != "turn_face_up" or len(batch.events) != 1:
        raise ValueError("A face-up copy continuation requires its exact special action")
    event, = batch.events
    if event.kind != FACE_UP_COPY_EVENT or event.payload[FACE_UP_COPY_RESULT] is not None:
        raise ValueError("A face-up copy continuation requires an undecided face-up event")
    subject = FaceUpCopySubject.from_dict(event.payload["subject"])
    if (subject.reference != response.get("card") or subject.controller != seat or not effects
            or any(effect.event_kind != FACE_UP_COPY_EVENT or effect.conditions != _face_up_copy_conditions(subject)
                   for effect in effects)):
        raise ValueError("The face-up copy does not belong to this payment subject")
    journal = response.get("_mana_replacement_selections")
    if isinstance(journal, Mapping) and event.event_id in journal:
        raise ValueError("A decided face-up choice cannot be pending again")
    prior = {event_id for event_id in journal if mana_damage_event_belongs_to_payment(event_id, response["_mana_payment_id"])} if isinstance(journal, Mapping) else set()
    return {event.event_id, *prior}, prior


def split_face_up_copy_payment(card, response):
    """Send the face-up replacement selection to the status owner after replaying costs."""
    journal = response.get("_mana_replacement_selections")
    if not isinstance(journal, Mapping):
        return response, ()
    event_id = face_up_copy_subject(card).occurrence
    if event_id not in journal:
        return response, ()
    choices = journal[event_id]
    if not isinstance(choices, (tuple, list)) or len(choices) != 1 or type(choices[0]) is not str or not choices[0]:
        raise ValueError("A face-up replacement journal must contain one offered choice")
    costs = {key: value for key, value in journal.items() if key != event_id}
    return {**response, "_mana_replacement_selections": costs}, tuple(choices)


COPY_HISTORY_FIELD = "scoped_copy_history"
ENTRY_COPY_HISTORY_FIELD = "entry_scoped_copy_history"


class CopyExpiry(StrEnum):
    FACE_DOWN = "face_down"
    END_OF_TURN = "end_of_turn"


@dataclass(frozen=True, slots=True)
class CopyValueState:
    """The copiable values and associated face choices of one form."""

    values: Mapping[str, Any] | None
    doors: Any = None
    timestamp: int = 0

    def __post_init__(self) -> None:
        from ..shared_type_doors import SharedTypeDoors
        from .immutable import FrozenMap

        if self.values is not None and not isinstance(self.values, Mapping):
            raise ValueError("A retained copy form requires detached values")
        if self.doors is not None and not isinstance(self.doors, SharedTypeDoors):
            raise ValueError("A retained copy form requires typed doors")
        if type(self.timestamp) is not int or self.timestamp < 0:
            raise ValueError("A retained copy form requires its canonical timestamp")
        if self.values is not None:
            object.__setattr__(self, "values", FrozenMap(self.values))

    def to_dict(self) -> dict[str, Any]:
        from .immutable import thaw_value

        return {"values": thaw_value(self.values), "doors": self.doors.to_dict() if self.doors is not None else None,
                "timestamp": self.timestamp}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CopyValueState":
        from ..shared_type_doors import SharedTypeDoors
        from .immutable import thaw_value

        if not isinstance(raw, Mapping) or set(raw) != {"values", "doors", "timestamp"}:
            raise ValueError("A retained copy form has a closed shape")
        doors = SharedTypeDoors.from_dict(thaw_value(raw["doors"])) if raw["doors"] is not None else None
        return cls(raw["values"], doors, raw["timestamp"])


@dataclass(frozen=True, slots=True)
class ScopedCopyValues:
    """Same-incarnation copy layers, ordered from earliest to latest."""

    baseline: CopyValueState
    ordered: tuple[tuple[CopyExpiry, CopyValueState], ...] = ()

    def __post_init__(self) -> None:
        if (not isinstance(self.baseline, CopyValueState)
                or any(not isinstance(scope, CopyExpiry) or not isinstance(value, CopyValueState)
                       or value.values is None for scope, value in self.ordered)
                or len({scope for scope, _ in self.ordered}) != len(self.ordered)):
            raise ValueError("Each common expiration requires its newest detached copy form")

    @property
    def effective(self) -> CopyValueState:
        return self.ordered[-1][1] if self.ordered else self.baseline

    def timestamp_effective(self, timestamp: int) -> "ScopedCopyValues":
        if not self.ordered:
            raise ValueError("Only an active copy layer can receive a timestamp")
        scope, form = self.ordered[-1]
        return replace(self, ordered=(*self.ordered[:-1], (scope, replace(form,timestamp=timestamp))))

    def capture_current(self, current: CopyValueState) -> "ScopedCopyValues":
        if self.effective.values != current.values:
            raise ValueError("The current copiable values do not match the retained copy history")
        if not self.ordered:
            return replace(self, baseline=current)
        return replace(self, ordered=(*self.ordered[:-1], (self.ordered[-1][0], current)))

    def apply(self, scope: CopyExpiry, value: CopyValueState) -> "ScopedCopyValues":
        kept = tuple(row for row in self.ordered if row[0] != scope)
        return replace(self, ordered=(*kept, (scope, value)))

    def expire(self, scope: CopyExpiry) -> "ScopedCopyValues":
        return replace(self, ordered=tuple(row for row in self.ordered if row[0] != scope))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "baseline": self.baseline.to_dict(),
                "ordered": [[scope.value, value.to_dict()] for scope, value in self.ordered]}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ScopedCopyValues":
        if (not isinstance(raw, Mapping) or set(raw) != {"schema_version", "baseline", "ordered"}
                or type(raw["schema_version"]) is not int or raw["schema_version"] != 1
                or not isinstance(raw["ordered"], (list, tuple))):
            raise ValueError("A copy history has a closed version-one shape")
        ordered = []
        for row in raw["ordered"]:
            if not isinstance(row, (list, tuple)) or len(row) != 2:
                raise ValueError("Each retained copy layer requires one expiry and form")
            ordered.append((CopyExpiry(row[0]), CopyValueState.from_dict(row[1])))
        return cls(CopyValueState.from_dict(raw["baseline"]), tuple(ordered))


def current_copy_history(card) -> ScopedCopyValues:
    current = CopyValueState(card.annotations.get("copy_overrides"), card.shared_type_doors)
    retained = card.annotations.get(COPY_HISTORY_FIELD)
    if retained is not None:
        history = ScopedCopyValues.from_dict(retained)
        return history.capture_current(replace(current,timestamp=history.effective.timestamp))
    legacy = card.annotations.get("until_end_of_turn") or {}
    if "copy_overrides_previous" in legacy:
        baseline = CopyValueState(legacy["copy_overrides_previous"], card.shared_type_doors)
        return ScopedCopyValues(baseline).apply(CopyExpiry.END_OF_TURN, current)
    return ScopedCopyValues(current)


def scoped_copy_form(values, *, timestamp=0) -> CopyValueState:
    from ..shared_type_doors import SharedTypeDoors, copied_room_door_ids

    faces = copied_room_door_ids(values)
    return CopyValueState(values, SharedTypeDoors(faces, printed=False) if faces is not None else None,timestamp)


UPKEEP_REFACING_MECHANIC = "fixed-upkeep-source-refacing"
UPKEEP_REFACING_TEMPLATE = "fixed-upkeep-source-refacing-v1"


def upkeep_refacing_condition():
    return {"all": [
        {"field": "player", "op": "eq", "value": "$source.controller"},
        {"field": "step", "op": "eq", "value": "upkeep"},
    ]}


def upkeep_refacing_effect():
    from ..face_up_effect_model import source_face_down_instruction

    return {"op": "offer_optional_effect", "player": "$controller", "effects": [source_face_down_instruction()]}


def source_refacing_component_capabilities(*, effects, target_schema, mechanic_ids):
    from ..entry_copy import FACE_UP_OR_ENTRY_COPY_CAPABILITY
    from ..face_up_effect_model import source_face_down_instruction

    if (target_schema is not None or UPKEEP_REFACING_MECHANIC not in mechanic_ids
            or tuple(effects) != (source_face_down_instruction(),)):
        return ()
    return (FACE_UP_OR_ENTRY_COPY_CAPABILITY,)


def is_closed_upkeep_refacing_program(program):
    from ..entry_copy import FACE_UP_OR_ENTRY_COPY_CAPABILITY
    from .immutable import thaw_value

    return (program.provenance.get("granted_only") is True
            and program.provenance.get("template_id") == UPKEEP_REFACING_TEMPLATE
            and program.event == "step.begin" and program.active_zone == "battlefield"
            and program.target_schema is None and program.cost_schema is None
            and thaw_value(program.event_condition) == upkeep_refacing_condition()
            and thaw_value(program.effects) == [upkeep_refacing_effect()]
            and {FACE_UP_OR_ENTRY_COPY_CAPABILITY, "effect.choice.optional_fixed"} <= set(program.capability_dependencies))
