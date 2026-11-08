from __future__ import annotations

"""Immutable unlocked designations for a split permanent with a shared type line."""

from dataclasses import dataclass
from typing import Any, Mapping


SHARED_TYPE_DOORS_VERSION = 2
DOOR_UNLOCKED_EVENT = "room.door_unlocked"
FULLY_UNLOCKED_EVENT = "room.fully_unlocked"
SHARED_TYPE_COPY_FIELD = "shared_type_copy_faces"
SHARED_TYPE_COPY_EXCEPTIONS = "shared_type_copy_exceptions"
SHARED_TYPE_FORM = "shared_type_split_permanent"
SHARED_TYPE_FORM_CAPABILITY = "permanent.shared_type_doors.lifecycle"
ROOM_UNLOCK_EVENT_CAPABILITY = "trigger.event.room_unlock"


@dataclass(frozen=True, slots=True)
class SharedTypeDoors:
    faces: tuple[str, str]
    unlocked: tuple[str, ...] = ()
    printed: bool = True

    def __post_init__(self) -> None:
        if (type(self.faces) is not tuple or len(self.faces) != 2
                or any(type(name) is not str or not name for name in self.faces)
                or self.faces[0] == self.faces[1]):
            raise ValueError("A shared type line requires two distinct door identities")
        if type(self.printed) is not bool:
            raise ValueError("Shared type door origin must be explicit")
        if type(self.unlocked) is not tuple or self.unlocked != tuple(
            face for face in self.faces if face in self.unlocked
        ):
            raise ValueError("Unlocked designations must follow the printed door order")

    @property
    def locked(self) -> tuple[str, ...]:
        return tuple(face for face in self.faces if face not in self.unlocked)

    def unlock(self, face: str) -> SharedTypeDoors:
        if face not in self.locked:
            raise ValueError("The selected door is unavailable to unlock")
        return SharedTypeDoors(self.faces, tuple(
            name for name in self.faces if name == face or name in self.unlocked
        ), self.printed)

    def rebind(self, faces: tuple[str, str]) -> SharedTypeDoors:
        return SharedTypeDoors(faces, tuple(
            name for old, name in zip(self.faces, faces, strict=True) if old in self.unlocked
        ), self.printed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1, "faces": list(self.faces), "unlocked": list(self.unlocked),
            "printed": self.printed,
        }

    @classmethod
    def from_dict(cls, value: Any) -> SharedTypeDoors:
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "faces", "unlocked", "printed"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or type(value["faces"]) is not list or type(value["unlocked"]) is not list):
            raise ValueError("Shared type door designation fields are malformed")
        return cls(tuple(value["faces"]), tuple(value["unlocked"]), value["printed"])


def printed_shared_type_faces(record: Any) -> tuple[Mapping[str, Any], ...]:
    if record is None or record.layout != "split" or len(record.faces) != 2:
        return ()
    left, right = record.faces
    type_line = left.get("type_line")
    if (not isinstance(type_line, str) or not type_line.strip() or type_line != right.get("type_line")
            or not record.is_permanent_spell or record.is_instant or record.is_sorcery):
        return ()
    return left, right


def shared_type_doors_enabled(state: Any) -> bool:
    return getattr(state, "split_permanent_face_values_version", None) == SHARED_TYPE_DOORS_VERSION


def copied_room_door_ids(values: Any) -> tuple[str, str] | None:
    if not isinstance(values, Mapping) or SHARED_TYPE_COPY_FIELD not in values:
        return None
    faces = values[SHARED_TYPE_COPY_FIELD]
    if (not isinstance(faces, (list, tuple)) or len(faces) != 2
            or any(not isinstance(face, Mapping) for face in faces)):
        raise ValueError("A copied shared type line requires two face values")
    return SharedTypeDoors(tuple(face.get("name") for face in faces)).faces


def shared_type_entry_doors(state: Any, card: Any, record: Any, *, enter_face: str | None) -> SharedTypeDoors | None:
    if not shared_type_doors_enabled(state) or card.face_down:
        return None
    overrides = card.annotations.get("copy_overrides")
    if overrides is not None:
        copied_faces = copied_room_door_ids(overrides)
        if copied_faces is None:
            return None
        doors = SharedTypeDoors(copied_faces, printed=bool(printed_shared_type_faces(record)) and card.is_card_object)
    else:
        faces = printed_shared_type_faces(record)
        if not faces:
            return None
        doors = SharedTypeDoors(tuple(face["name"] for face in faces))
    if card.zone == "stack":
        return doors.unlock(enter_face or card.active_face)
    return doors
