from __future__ import annotations

"""Intrinsic Omen face identity and successful resolution."""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

from .cast_face_offers import (
    CURRENT_CAST_FACE_OFFERS_VERSION,
    OMEN_CONTEXT,
    OmenResolution,
    omen_resolution_binding,
)
from .characteristic_evaluation import type_parts

if TYPE_CHECKING:
    from .model import CardInstance, StackItem


OMEN_CAPABILITY = "casting.lifecycle.omen"


class OmenCardRecord(Protocol):
    @property
    def layout(self) -> str: ...

    @property
    def faces(self) -> tuple[Mapping[str, Any], ...]: ...


def omen_faces(record: OmenCardRecord) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """Return the validated normal and alternative faces of an Omen card."""
    if record.layout != "adventure":
        return None
    faces = [
        (face, type_parts(str(face.get("type_line") or "")))
        for face in record.faces
    ]
    alternatives = [(face, types) for face, (types, subtypes, _) in faces if "omen" in subtypes]
    if not alternatives:
        return None
    alternative, types = alternatives[0]
    names = [face.get("name") for face in record.faces]
    if (
        len(names) != 2 or len(alternatives) != 1 or names[0] == names[1]
        or any(type(name) is not str or not name for name in names)
        or not types.intersection({"instant", "sorcery"})
    ):
        raise ValueError("Omen requires one instant or sorcery alternative and a distinct normal face")
    normal = next(face for face in record.faces if face is not alternative)
    return normal, alternative


def omen_stack_context(
    record: OmenCardRecord, card: CardInstance, *, cast_face_version: int | None,
) -> dict[str, dict[str, str | int]]:
    if cast_face_version != CURRENT_CAST_FACE_OFFERS_VERSION:
        return {}
    faces = omen_faces(record)
    if faces is None or card.active_face != faces[1]["name"]:
        return {}
    return {OMEN_CONTEXT: OmenResolution(faces[1]["name"]).to_dict()}


def omen_characteristic_face(
    card: CardInstance, record: OmenCardRecord, *, cast_face_version: int | None,
) -> Mapping[str, Any] | None:
    if cast_face_version != CURRENT_CAST_FACE_OFFERS_VERSION:
        return None
    faces = omen_faces(record)
    if faces is None:
        return None
    normal, alternative = faces
    # Actual departures clear the selected spell face. Detached casting views
    # also use active_face to carry their prospective selection.
    return alternative if card.active_face == alternative["name"] else normal


def omen_resolution_destination(item: StackItem, destination: str | None) -> str | None:
    if omen_resolution_binding(item) is not None and destination in {None, "graveyard"}:
        return "library"
    return destination
