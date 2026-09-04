from __future__ import annotations

"""Canonical logical-object-aware CR 701.27 transform mutation."""

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .card_program_faces import program_matches_face, selected_face_id
from .day_night_model import (
    DAYBOUND_TEMPLATE_ID,
    DayNightBoundMode,
    NIGHTBOUND_TEMPLATE_ID,
)
from .model import CardInstance, StackItem
from .trigger_processing import enqueue_trigger_batch


TRANSFORM_OPERATION = "transform"
TRANSFORM_EVENT = "permanent.transforms"


class PermanentTransformError(ValueError):
    """A transform request violates the closed typed boundary."""


class PermanentTransformHost(Protocol):
    state: Any
    seats: Sequence[str]
    semantics: Any

    def card_record(self, card: CardInstance) -> Any: ...

    def _effective_card_data(
        self, card: CardInstance
    ) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> list[CardInstance]: ...

    def _dispatch_semantic_event(
        self,
        event: str,
        context: Mapping[str, Any],
        *,
        sources: Sequence[CardInstance] | None = None,
        trigger_batch: list[StackItem] | None = None,
    ) -> list[str]: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
        *,
        importance: int = 1,
        changed_objects: Sequence[str] = (),
        changed_players: Sequence[str] = (),
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class PermanentTransformResult:
    object_id: str
    card_ref: str
    logical_object_id: str
    previous_face: str
    current_face: str
    transform_count: int
    controller: str


def _bound_program_mode(program: Any) -> DayNightBoundMode | None:
    provenance = getattr(program, "provenance", {})
    template = str(
        provenance.get("template_id")
        if isinstance(provenance, Mapping)
        else ""
    )
    if template == DAYBOUND_TEMPLATE_ID:
        return DayNightBoundMode.DAYBOUND
    if template == NIGHTBOUND_TEMPLATE_ID:
        return DayNightBoundMode.NIGHTBOUND
    return None


def current_day_night_bound_mode(
    host: PermanentTransformHost,
    card: CardInstance,
) -> DayNightBoundMode | None:
    """Return the applicable compiled bound ability on the current face."""

    if (
        card.zone != "battlefield"
        or card.phased_out
        or card.face_down
        or card.object_kind != "card"
        or card.annotations.get("copied_from") is not None
        or card.annotations.get("copy_overrides") is not None
    ):
        return None
    record = host.card_record(card)
    if (
        record is None
        or getattr(record, "layout", None) != "transform"
        or len(getattr(record, "faces", ())) != 2
    ):
        return None
    characteristics = host._effective_card_data(card)
    # Import lazily so the shared applicability query remains the sole
    # layer-6 parent/component boundary without creating an import cycle.
    from .trigger_discovery import program_has_current_ability_fragments

    found: list[DayNightBoundMode] = []
    for program in host.semantics.programs_for_oracle(
        record.oracle_id,
        active_zone="battlefield",
    ):
        mode = _bound_program_mode(program)
        if (
            mode is None
            or not program_matches_face(record, program, card)
            or not host.semantic_program_is_current_trusted(program)
            or not program_has_current_ability_fragments(
                program,
                characteristics,
            )
        ):
            continue
        found.append(mode)
    return found[0] if len(found) == 1 else None


def trusted_daybound_entry_face(
    host: PermanentTransformHost,
    card: CardInstance,
    *,
    prospective_face: str | None,
) -> str | None:
    """Select the back face for one compiled Daybound night entry."""

    if (
        host.state.day_night != "night"
        or card.object_kind != "card"
        or card.face_down
        or card.annotations.get("copied_from") is not None
        or card.annotations.get("copy_overrides") is not None
    ):
        return prospective_face
    record = host.card_record(card)
    faces = tuple(getattr(record, "faces", ())) if record is not None else ()
    if getattr(record, "layout", None) != "transform" or len(faces) != 2:
        return prospective_face
    front_name = str(faces[0].get("name") or "")
    selected = prospective_face or front_name
    if selected != front_name:
        return prospective_face
    daybound = [
        program
        for program in host.semantics.programs_for_oracle(
            record.oracle_id,
            active_zone="battlefield",
        )
        if _bound_program_mode(program) is DayNightBoundMode.DAYBOUND
        and program_matches_face(
            record,
            program,
            card,
            prospective_name=front_name,
        )
        and host.semantic_program_is_current_trusted(program)
    ]
    return str(faces[1].get("name") or "") if len(daybound) == 1 else prospective_face


def _other_face(host: PermanentTransformHost, card: CardInstance) -> tuple[str, str] | None:
    record = host.card_record(card)
    faces = tuple(getattr(record, "faces", ())) if record is not None else ()
    if (
        record is None
        or getattr(record, "layout", None) != "transform"
        or len(faces) != 2
    ):
        return None
    names = tuple(str(face.get("name") or "") for face in faces)
    if any(not name for name in names) or names[0] == names[1]:
        raise PermanentTransformError("Transforming faces must have unique names")
    current = selected_face_id(record, card)
    if current not in names:
        raise PermanentTransformError("Active transform face is not in its card record")
    target_index = 1 if current == names[0] else 0
    target_types, _, _ = host._type_parts(
        str(faces[target_index].get("type_line") or "")
    )
    if target_types.intersection({"instant", "sorcery"}):
        return None
    return current, names[target_index]


def transform_permanent(
    host: PermanentTransformHost,
    card: CardInstance,
    *,
    expected_transform_count: int | None,
    day_night_instruction: bool,
) -> PermanentTransformResult | None:
    """Commit one face change without changing logical object or timestamp."""

    if type(day_night_instruction) is not bool:
        raise PermanentTransformError("Transform instruction kind must be explicit")
    if not day_night_instruction and expected_transform_count is None:
        raise PermanentTransformError(
            "Activated or triggered transforms require a count snapshot"
        )
    if expected_transform_count is not None and (
        type(expected_transform_count) is not int
        or expected_transform_count < 0
    ):
        raise PermanentTransformError(
            "Expected transform count must be nonnegative or null"
        )
    if (
        card.zone != "battlefield"
        or card.phased_out
        or card.face_down
        or card.object_kind != "card"
        or card.annotations.get("copied_from") is not None
        or card.annotations.get("copy_overrides") is not None
        or (
            expected_transform_count is not None
            and card.transform_count != expected_transform_count
        )
        or (
            not day_night_instruction
            and current_day_night_bound_mode(host, card) is not None
        )
    ):
        return None
    faces = _other_face(host, card)
    if faces is None:
        return None
    previous_face, current_face = faces
    card.active_face = current_face
    card.transform_count += 1
    return PermanentTransformResult(
        object_id=card.object_id,
        card_ref=card.ref,
        logical_object_id=card.logical_object_id,
        previous_face=previous_face,
        current_face=current_face,
        transform_count=card.transform_count,
        controller=card.controller,
    )


def commit_transform_batch(
    host: PermanentTransformHost,
    cards: Sequence[CardInstance],
    *,
    reason: str,
    day_night_instruction: bool,
    expected_transform_counts: Mapping[str, int] | None = None,
    trigger_batch: list[StackItem] | None = None,
) -> tuple[PermanentTransformResult, ...]:
    """Transform a simultaneous set, then expose normalized events."""

    if type(reason) is not str or not reason:
        raise PermanentTransformError("Transform reason must be nonempty")
    expected = dict(expected_transform_counts or {})
    object_ids = {card.object_id for card in cards}
    if len(object_ids) != len(cards):
        raise PermanentTransformError("Transform batch contains duplicates")
    if set(expected) - object_ids or (
        not day_night_instruction and set(expected) != object_ids
    ):
        raise PermanentTransformError(
            "Transform count snapshots must match the requested batch"
        )
    results = tuple(
        result
        for card in cards
        if (
            result := transform_permanent(
                host,
                card,
                expected_transform_count=expected.get(card.object_id),
                day_night_instruction=day_night_instruction,
            )
        )
        is not None
    )
    if not results:
        return ()
    for result in results:
        host._log(
            result.controller,
            "permanent.transform",
            f"{result.card_ref} transformed into {result.current_face}.",
            {
                "object": result.card_ref,
                "from_face": result.previous_face,
                "to_face": result.current_face,
                "transform_count": result.transform_count,
                "reason": reason,
            },
            importance=2,
            changed_objects=[result.object_id],
            changed_players=[result.controller],
        )
    owns_batch = trigger_batch is None
    waiting = trigger_batch if trigger_batch is not None else []
    sources = host._semantic_event_sources(zones={"battlefield"})
    for result in results:
        host._dispatch_semantic_event(
            TRANSFORM_EVENT,
            {
                "card": result.card_ref,
                "object_ref": result.card_ref,
                "object_id": result.object_id,
                "logical_object_id": result.logical_object_id,
                "controller": result.controller,
                "from_face": result.previous_face,
                "to_face": result.current_face,
                "transform_count": result.transform_count,
                "reason": reason,
            },
            sources=sources,
            trigger_batch=waiting,
        )
    if owns_batch:
        enqueue_trigger_batch(host, waiting)
    return results


__all__ = [
    "PermanentTransformError",
    "PermanentTransformHost",
    "PermanentTransformResult",
    "TRANSFORM_EVENT",
    "TRANSFORM_OPERATION",
    "commit_transform_batch",
    "current_day_night_bound_mode",
    "transform_permanent",
    "trusted_daybound_entry_face",
]
