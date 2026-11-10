from __future__ import annotations

"""Canonical face binding for compiler-pinned runtime programs."""

from typing import Any
from dataclasses import replace
from .defined_token_faces import DEFINED_TOKEN_FACES_FIELD,defined_token_current_characteristics


def prospective_face_view(record: Any, card: Any, prospective_name: str | None) -> Any:
    """Select an existing face on an isolated view for prospective rules queries."""
    if not prospective_name or not any(str(face.get('name') or '') == prospective_name
            for face in getattr(record, 'faces', ())):
        return card
    return replace(card, active_face=prospective_name, annotations={**card.annotations})


def selected_face_id(
    record: Any,
    card: Any | None = None,
    *,
    prospective_name: str | None = None,
) -> str:
    if prospective_name:
        if getattr(record, "faces", ()):
            for face in record.faces:
                name = str(face.get("name") or "")
                if name == prospective_name:
                    return name
        elif prospective_name == str(getattr(record, "name", "")):
            return "front"
    active_face = str(getattr(card, "active_face", None) or "")
    copied_values = (getattr(card, 'annotations', {}) or {}).get('copy_overrides', {})
    if DEFINED_TOKEN_FACES_FIELD in copied_values:
        copied_values=defined_token_current_characteristics(card,copied_values)
    copied_face = copied_values.get('_copiable_face_id')
    if copied_face:
        active_face = copied_face
    if active_face:
        return active_face
    faces = getattr(record, "faces", ())
    if faces:
        return str(faces[0].get("name") or "front")
    return "front"


def normalized_program_face_id(record: Any, program: Any) -> str:
    face_id = str(program.provenance.get("face_id") or "").strip()
    faces = tuple(getattr(record, "faces", ()))
    if face_id == "front" and faces:
        return str(faces[0].get("name") or "front")
    if face_id == "back" and len(faces) == 2:
        return str(faces[1].get("name") or "back")
    return face_id or "front"


def current_shared_type_doors(card: Any):
    """Return the printed door state when its shared type line is current."""
    if card is None:
        return None
    doors = getattr(card, "shared_type_doors", None)
    overrides = (getattr(card, "annotations", {}) or {}).get("copy_overrides")
    if overrides is None:
        return doors if doors is not None and doors.printed else None
    from .shared_type_doors import SharedTypeDoors, copied_room_door_ids
    copied = copied_room_door_ids(overrides)
    if copied is None or (card.zone != "battlefield" and doors is None):
        return None
    return doors.rebind(copied) if doors is not None else SharedTypeDoors(copied, printed=False)


def room_program_face_is_active(card: Any, program: Any) -> bool:
    doors = current_shared_type_doors(card)
    if doors is None:
        return True
    face = program.provenance.get("face_id")
    if face == "front":
        face = doors.faces[0]
    elif face == "back":
        face = doors.faces[1]
    return face in doors.unlocked


def program_matches_face(
    record: Any,
    program: Any,
    card: Any | None = None,
    *,
    prospective_name: str | None = None,
) -> bool:
    if current_shared_type_doors(card) is not None:
        return room_program_face_is_active(card, program)
    return normalized_program_face_id(record, program) == selected_face_id(
        record,
        card,
        prospective_name=prospective_name,
    )


__all__ = [
    "normalized_program_face_id",
    "program_matches_face",
    "selected_face_id",
    "prospective_face_view",
]
