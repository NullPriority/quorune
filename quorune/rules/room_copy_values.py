from __future__ import annotations

"""Copiable shared-type split faces and their current unlocked projection."""

from copy import deepcopy
from dataclasses import replace
from typing import Any, Mapping

from ..card_program_faces import current_shared_type_doors
from ..defined_token_faces import CopiableTokenFace
from ..shared_type_doors import (
    SHARED_TYPE_COPY_EXCEPTIONS, SHARED_TYPE_COPY_FIELD, SharedTypeDoors,
    copied_room_door_ids, printed_shared_type_faces, shared_type_doors_enabled,
)


def capture_room_copy_faces(host: Any, card: Any, record: Any) -> list[dict[str, Any]]:
    if (not shared_type_doors_enabled(host.state) or card.face_down
            or card.annotations.get("copy_overrides") is not None
            or card.zone not in {"stack", "battlefield"}):
        return []
    printed = printed_shared_type_faces(record)
    if not printed:
        return []
    identities = tuple(face["name"] for face in printed)
    from ..carddb_characteristics import shared_type_copy_display_values

    result = []
    for identity in identities:
        designations = SharedTypeDoors(identities, (identity,)) if card.zone == "battlefield" else None
        view = replace(card, active_face=identity, shared_type_doors=designations)
        values = host._compiled_base_characteristics(view, record)
        result.append(CopiableTokenFace(identity, shared_type_copy_display_values(values)).to_dict())
    return result


def _copy_faces(values: Mapping[str, Any]) -> tuple[CopiableTokenFace, CopiableTokenFace]:
    copied_room_door_ids(values)
    first, second = (CopiableTokenFace.from_dict(face) for face in values[SHARED_TYPE_COPY_FIELD])
    if first.type_line != second.type_line:
        raise ValueError("Copied split doors must share one type line")
    return first, second


def _copy_exceptions(values: Mapping[str, Any]):
    from ..token_copy_recipes import TokenCopyExceptionSpec

    exceptions = values.get(SHARED_TYPE_COPY_EXCEPTIONS, ())
    if not isinstance(exceptions, (list, tuple)):
        raise ValueError("Copied split door exceptions must be an array")
    return tuple(TokenCopyExceptionSpec.from_dict(value) for value in exceptions)


def room_copy_unlock_costs(values: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    no_cost = any(exception.remove_mana_cost for exception in _copy_exceptions(values))
    return tuple({
        "name": face.name,
        "mana_cost": "" if no_cost else face.values["mana_cost"],
    } for face in _copy_faces(values))


def room_copy_characteristics(card: Any, values: Mapping[str, Any]) -> dict[str, Any]:
    if SHARED_TYPE_COPY_FIELD not in values:
        return dict(values)
    faces = _copy_faces(values)
    doors = current_shared_type_doors(card)
    active = doors.unlocked if doors is not None else (card.active_face,)
    selected = tuple(face.characteristics() for face in faces if face.name in active)
    result = _combine_face_values(selected, faces[0].type_line)
    from ..token_copy_recipes import apply_copy_exception
    for exception in _copy_exceptions(values):
        result = apply_copy_exception(result, exception)
    return {**dict(values), **result}


def _combine_face_values(selected: tuple[Mapping[str, Any], ...], type_line: str) -> dict[str, Any]:
    fields = {
        "name": " // ".join(face["name"] for face in selected),
        "mana_cost": " // ".join(face["mana_cost"] for face in selected),
        "mana_value": sum(face["mana_value"] for face in selected),
        "type_line": type_line,
        "display_text": "\n".join(face.get("display_text", "") for face in selected),
        "oracle_text": "", "executable_oracle_text": "",
        "power": None, "toughness": None, "loyalty": None, "defense": None,
    }
    for key in ("colors", "keywords", "produced_mana"):
        fields[key] = list(dict.fromkeys(value for face in selected for value in face.get(key, ())))
    for key in ("ability_fragments", "activated_abilities"):
        fields[key] = [deepcopy(value) for face in selected for value in face.get(key, ())]
    return fields
